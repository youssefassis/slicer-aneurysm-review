import functools
import getpass
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import qt

# The Slicer-free core: geometry, detection file format, the review record and display
# maths. See ReformatVizLib/__init__.py for why the boundary exists.
import ReformatVizLib
import slicer
import vtk
import vtkmodules
from ReformatVizLib import case, geometry, jsonio, rendering, table
from ReformatVizLib import detections as det
from ReformatVizLib import review as rev
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin
from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction


def makeScalarOpacity():
    scalarOpacity = vtkPiecewiseFunction()
    scalarOpacity.AllowDuplicateScalarsOn()
    scalarOpacity.RemoveAllPoints()
    for x in range(rendering.SCALAR_OPACITY_POINT_COUNT):
        scalarOpacity.AddPoint(float(x), 0.0)
    return scalarOpacity


# Enough to look round at the few millimetres these spheres span on screen. The mesh is
# rebuilt on every mouse move while an annotation point is dragged, and one sphere is resident
# per detection, so 100x100 (~10k points each) cost far more than it showed.
SPHERE_RESOLUTION = 32

#: The review table's columns, in order. add_detection_to_table fills a row in this order.
TABLE_COLUMNS = ["Confidence (%)", "Diameter (mm)", "Verdict", "Not because", "Likert", "Note"]
COL_CONFIDENCE, COL_DIAMETER, COL_VERDICT, COL_REASON, COL_LIKERT, COL_NOTE = range(6)

#: Set this to attribute reviews to someone other than the logged-in user, from the Python
#: console: slicer.app.userSettings().setValue('ReformatViz/Reviewer', 'ykerrien')
REVIEWER_SETTING = "ReformatViz/Reviewer"


def reviewerName():
    """Return who a review made now is attributed to.

    Slicer has no notion of a signed-in user, so this is the OS account unless the setting
    above says otherwise. It ends up in the review file and in its file name.
    """
    configured = slicer.app.userSettings().value(REVIEWER_SETTING)
    return str(configured) if configured else getpass.getuser()


def toolDescription():
    """Return the build string recorded in the review — its only traceability."""
    return f"ReformatViz {ReformatVizLib.__version__} (Slicer {slicer.app.applicationVersion})"


def now():
    """Return the timestamp to record. UTC, so reviews from two sites can be ordered."""
    # Slicer 5.12 runs Python 3.12, so datetime.UTC is available; requires-python is 3.11.
    return datetime.now(UTC).isoformat()


def createSphere(center, radius):
    sphere = vtk.vtkSphereSource()
    sphere.SetCenter(center)
    sphere.SetRadius(radius)
    sphere.SetPhiResolution(SPHERE_RESOLUTION)
    sphere.SetThetaResolution(SPHERE_RESOLUTION)
    sphere.Update()
    return sphere


def createModel(model, name):
    node = slicer.modules.models.logic().AddModel(model.GetOutput())
    node.SetName(name)
    node.GetDisplayNode().SetVisibility2D(True)
    node.GetDisplayNode().SetSliceIntersectionThickness(2)
    return node


class ReformatViz(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "Candidate Review"
        self.parent.categories = ["Annotation"]
        self.parent.dependencies = []
        self.parent.contributors = ["Youssef Assis and Erwan Kerrien (Loria lab.)"]
        self.parent.acknowledgementText = (
            "This module was developed by Youssef Assis, and is maintained by Erwan Kerrien"
        )


class ReformatVizWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    def __init__(self, parent=None):
        """
        Called when the user opens the module the first time and the widget is initialized.
        """
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)  # needed to observe the scene being closed

    def setup(self):
        """
        Called when the user opens the module the first time and the widget is initialized.
        """
        ScriptedLoadableModuleWidget.setup(self)

        # Load widget from .ui file (created by Qt Designer).
        # Additional widgets can be instantiated manually and added to self.layout.
        uiWidget = slicer.util.loadUI(self.resourcePath("UI/ReformatViz.ui"))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)
        uiWidget.setMRMLScene(slicer.mrmlScene)

        self.connectVolumeControls()
        self.connectAnnotationControls()
        self.connectReviewControls()
        self.connectViewControls()
        self.initState()
        self.buildReviewTable()

        # Last, so that a scene closed from the File menu can never land on a half-built
        # widget: the handler reads self.detections and the table.
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

    def enter(self):
        """Called when the reviewer switches to this module.

        No call up to `ScriptedLoadableModuleWidget`: it does not define `enter`/`exit`, and
        Slicer swallows the resulting AttributeError while switching modules, so everything
        after such a call would silently never run.
        """
        probe = self.dataProbeWidget()
        if probe:
            # Remembered rather than assumed: hiding it is this module's doing, and a
            # reviewer who had already collapsed it should not find it reopened on the way
            # out.
            self.dataProbeWasVisible = probe.visible
            probe.setVisible(False)

    def exit(self):
        """Called when the reviewer switches away to another module. See `enter`."""
        probe = self.dataProbeWidget()
        if probe:
            probe.setVisible(self.dataProbeWasVisible)

    def dataProbeWidget(self):
        """Return the application's data probe, or None when there is not one to hide.

        The module hides it to make room for the review table. It belongs to the application
        rather than to this module, which is why it is put back in `exit()` instead of being
        hidden once and for the rest of the session.
        """
        mainWindow = slicer.util.mainWindow()
        if mainWindow is None:
            return None
        try:
            return slicer.util.findChild(mainWindow, "DataProbeCollapsibleWidget")
        except RuntimeError:
            # findChild raises rather than returning None, and the widget is genuinely absent
            # in a session started without the DataProbe module.
            return None

    def connectVolumeControls(self):
        self.ui.load_volume_button.clicked.connect(self.load_files)  # load volume
        self.ui.vol_visibility.stateChanged.connect(self.set_volume_visibility)
        self.ui.vol_translucent.stateChanged.connect(self.set_volume_translucent)
        self.ui.cropBox.stateChanged.connect(self.onCropChanged)
        self.ui.cropVisBox.stateChanged.connect(self.onCropVisibilityChanged)

        self.ui.volumeOpacity.valueChanged.connect(self.onVolumeOpacityChanged)
        self.ui.volumeThreshold.valueChanged.connect(self.onVolumeThresholdChanged)

        self.ui.volumeColor.colorChanged.connect(self.set_volume_color)

    def connectAnnotationControls(self):
        self.ui.SpheresVisibility.stateChanged.connect(self.set_spheres_visibility)
        self.ui.LinesVisibility.stateChanged.connect(self.set_lines_visibility)
        self.ui.SpheresOpacity.valueChanged.connect(self.set_spheres_opacity)

        self.ui.SpheresColorPicker.colorChanged.connect(self.set_spheres_color)
        self.ui.LinesColorPicker.colorChanged.connect(self.set_lines_color)
        self.ui.ConfidenceColorPicker.colorChanged.connect(self.set_confidence_color)
        self.ui.ConfidenceVisibility.stateChanged.connect(self.set_confidence_visibility)

        self.ui.MarkupsColorPicker.colorChanged.connect(self.set_markups_color)
        self.ui.MarkupsVisibility.stateChanged.connect(self.set_markups_visibility)

        self.ui.addButton.connect("clicked(bool)", self.addAnnotationNode)
        self.ui.cancelButton.connect("clicked(bool)", self.removeAnnotationNodes)
        self.ui.okButton.connect("clicked(bool)", self.validateAnnotation)

    def connectReviewControls(self):
        self.ui.signOffButton.clicked.connect(self.onSignOff)
        # The lambda swallows clicked's bool argument, which would otherwise bind to
        # reset_view: the button always emits False, so connecting the slot directly meant
        # unselecting never reset the view.
        self.ui.unselectButton.connect("clicked(bool)", lambda *_: self.unselectAllRows())
        self.ui.cleanButton.connect("clicked(bool)", self.reset)

    def connectViewControls(self):
        self.ui.axialVisibility.stateChanged.connect(self.set_axial_slice_visibility)
        self.ui.rotateAxisSlider.valueChanged.connect(self.rotateAxisSlider)
        self.ui.centerCamera.clicked.connect(self.centerCamera)
        self.ui.thicknessSlider.valueChanged.connect(self.thicknessSlider)

    def initState(self):
        """Create the session-long nodes and the state the rest of the widget assumes."""
        self.dataProbeWasVisible = True
        self.ensureMarkupsNodes()
        self.spheres = []
        self.lines = []

        self.selected_detection = -1
        self.detections = []

        self.tmpMark: slicer.vtkMRMLMarkupsFiducialNode = None
        self.tmpSphereSource: slicer.vtkSphereSource = None
        self.tmpSphere: slicer.vtkMRMLModelNode = None

        self.volNode: slicer.vtkMRMLScalarVolumeNode = None
        self.volProp: vtkmodules.vtkRenderingCore.vtkVolumeProperty = None
        self.roiNode: slicer.vtkMRMLMarkupsROINode = None
        self.renderingRoiNode: slicer.vtkMRMLMarkupsROINode = None
        self.scalarOpacity = makeScalarOpacity()

        # The review of the case on screen, and where it is written. None means no case is
        # open, and nothing is recorded.
        self.review = None
        self.reviewPath = None
        self.autosaveFailed = False
        # A view angle changed in memory but not yet on disk; see saveViewAngle.
        self.angleUnsaved = False

        self.prepare_slices()

    def buildReviewTable(self):
        """Build the review table. Its signals are connected here, once — see the note in
        add_detection_to_table, which runs per row."""
        self.reviewSectionTitle = self.ui.CollapsibleButton.text
        self.populatingTable = False
        self.tableWidget = qt.QTableWidget()
        self.tableWidget.setColumnCount(len(TABLE_COLUMNS))
        self.tableWidget.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.tableWidget.horizontalHeader().setStretchLastSection(True)
        self.tableWidget.resizeColumnsToContents()
        self.tableWidget.setSelectionBehavior(self.tableWidget.SelectRows)
        self.tableWidget.setSelectionMode(self.tableWidget.SingleSelection)
        # The current cell follows the arrow keys as well as the mouse, so the view does too.
        # A click is still needed for the one move it cannot see: back onto the row that was
        # current before "Unselect all".
        self.tableWidget.currentCellChanged.connect(self.onCurrentCellChanged)
        self.tableWidget.clicked.connect(self.onRowClicked)
        # Once, not per row: the note cells are ordinary items, so one table-level signal
        # covers all of them however many rows arrive.
        self.tableWidget.itemChanged.connect(self.onNoteEdited)

        self.layout_2 = qt.QGridLayout()
        self.layout_2.addWidget(self.tableWidget)
        self.ui.CollapsibleButton.setLayout(self.layout_2)

    def volumeRenderingDisplayNode(self):
        """Return the volume's rendering display node, or None if there is none to reach.

        None also when the Volume Rendering module itself is gone: Slicer unloads it before
        this widget at exit, and cleanup() still has the case to close.
        """
        module = getattr(slicer.modules, "volumerendering", None)
        if not self.volNode or module is None:
            return None
        return module.logic().GetFirstVolumeRenderingDisplayNode(self.volNode)

    def set_volume_color(self, color=None):
        # The volume can outlive its rendering nodes, and every other call site here guards
        # for it; without this the colour picker raises instead of doing nothing.
        displayNode = self.volumeRenderingDisplayNode()
        propertyNode = displayNode.GetVolumePropertyNode() if displayNode else None
        if not propertyNode:
            return

        color = rendering.to_rgb(color if color is not None else self.ui.volumeColor.color)
        rangeCT = [0] * 2
        propertyNode.GetColor().GetRange(rangeCT)
        colorTransferFunction = vtk.vtkColorTransferFunction()
        colorTransferFunction.AddRGBPoint(rangeCT[0], color[0], color[1], color[2])
        colorTransferFunction.AddRGBPoint(rangeCT[1], color[0], color[1], color[2])
        propertyNode.SetColor(colorTransferFunction)

    def updateScalarOpacity(self):
        if self.volProp:
            points = rendering.scalar_opacity_points(
                min_value=self.ui.volumeThreshold.minimum,
                max_value=self.ui.volumeThreshold.maximum,
                threshold=self.ui.volumeThreshold.value,
                opacity=self.ui.volumeOpacity.value,
                translucent=self.ui.vol_translucent.checked,
            )
            for i, point in enumerate(points):
                self.scalarOpacity.SetNodeValue(i, point)
            self.volProp.GetScalarOpacity().DeepCopy(self.scalarOpacity)

    def onVolumeThresholdChanged(self, value):
        self.updateScalarOpacity()

    def onVolumeOpacityChanged(self, value):
        self.updateScalarOpacity()

    def currentRoiNode(self):
        """Return the module's crop ROI if it is in the scene, else None.

        Held as a reference rather than looked up by class: the first ROI in the scene is not
        necessarily this module's, and a scene closed from the File menu takes ours with it
        while the widget keeps pointing at it.
        """
        if self.roiNode is not None and slicer.mrmlScene.IsNodePresent(self.roiNode):
            return self.roiNode
        self.roiNode = None
        return None

    def ensureRoiNode(self):
        """Return the crop ROI, creating it the first time and after a scene close."""
        roiNode = self.currentRoiNode()
        if roiNode is None:
            roiNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsROINode")
            roiNode.SetName("")  # undisplay ROI name
            roiNode.GetDisplayNode().SetOpacity(0.05)
            roiNode.GetDisplayNode().SetVisibility(True)
            self.roiNode = roiNode
        return roiNode

    def onCropVisibilityChanged(self):
        roiNode = self.currentRoiNode()
        if roiNode:
            roiNode.GetDisplayNode().SetVisibility(self.ui.cropVisBox.checked)

    def onCropChanged(self):
        if (not self.ui.cropBox.checked) or (not self.volNode):
            if self.currentRoiNode():
                self.ui.cropVisBox.setChecked(False)
                # This branch is also the "no volume loaded" one, which has no display node.
                displayNode = self.volumeRenderingDisplayNode()
                if displayNode:
                    displayNode.SetCroppingEnabled(0)
        else:
            self.ui.cropVisBox.setChecked(True)
            roiNode = self.ensureRoiNode()
            if self.selected_detection >= 0:
                position_RAS = self.detections[self.selected_detection]["center"]
                radius = [self.detections[self.selected_detection]["radius"] * 4] * 3
                roiNode.SetCenter(position_RAS)
                roiNode.SetRadiusXYZ(radius)
            else:
                m = vtkmodules.vtkCommonMath.vtkMatrix4x4()
                self.volNode.GetIJKToRASMatrix(m)
                center, size = geometry.volume_bounds(
                    slicer.util.arrayFromVTKMatrix(m), self.volNode.GetImageData().GetDimensions()
                )
                roiNode.SetCenter(center.tolist())
                roiNode.SetSize(size.tolist())
            displayNode = self.volumeRenderingDisplayNode()
            if displayNode:
                displayNode.SetCroppingEnabled(True)
                displayNode.SetAndObserveROINodeID(roiNode.GetID())
                displayNode.GetROINode().SetDisplayVisibility(True)

    def rotateAxisSlider(self, value):
        node = slicer.mrmlScene.GetNodeByID("vtkMRMLSliceNodeRed")
        if node is not None:
            if self.detections and self.selected_detection >= 0:
                d = self.detections[self.selected_detection]
                d["viewAngle"] = value
                entry = self.reviewEntry(d)
                # prepare_slices re-applies the stored angle on every row click; only a real
                # change needs writing out.
                if entry and entry["viewAngle"] != value:
                    rev.set_view_angle(self.review, d[det.SOURCE_KEY], value)
                    self.angleUnsaved = True
            self.reformatLogic.SetSliceNormal(node, geometry.slice_normal(self.candidateRz, value))
            # Read back the slice axes Slicer settled on, then roll in-plane so the
            # candidate stays upright instead of tumbling as the reviewer rotates.
            m = node.GetSliceToRAS()
            sliceX = np.array(m.MultiplyDoublePoint([1, 0, 0, 0]))[:3]
            sliceY = np.array(m.MultiplyDoublePoint([0, 1, 0, 0]))[:3]
            self.reformatLogic.RotateSlice(node, 2, geometry.in_plane_roll_deg(sliceX, sliceY, self.candidateAxis))
            self.reformatLogic.SetSliceOrigin(node, self.candidateOrigin)

        node = slicer.mrmlScene.GetNodeByID("vtkMRMLSliceNodeGreen")
        if node is not None:
            self.reformatLogic.SetSliceOrigin(node, self.candidateOrigin)
        node = slicer.mrmlScene.GetNodeByID("vtkMRMLSliceNodeYellow")
        if node is not None:
            self.reformatLogic.SetSliceOrigin(node, self.candidateOrigin)

    def set_axial_slice_visibility(self):
        layoutManager = slicer.app.layoutManager()
        controller = layoutManager.sliceWidget("Red").sliceController()
        controller.setSliceVisible(self.ui.axialVisibility.checked)

    def thicknessSlider(self, value):
        node = slicer.mrmlScene.GetNodeByID("vtkMRMLSliceNodeRed")
        if node is None:
            return
        node.SetSlabReconstructionEnabled(1)
        node.SetSlabReconstructionType(1)  # Max Intensity Projection
        node.SetSlabReconstructionThickness(value)

    def load_files(self):
        # save before loading
        dir_path = qt.QFileDialog().getExistingDirectory()
        if dir_path:
            self.openCase(Path(dir_path))

    def openCase(self, directory):
        """Open the case in ``directory``: its volume, its detections and its review.

        Separate from `load_files` so the whole path can be exercised without the directory
        dialog, which a test cannot open.
        """
        # Which files make up a case is decided in ReformatVizLib, where it can be tested;
        # the review the module itself writes into this directory is not one of them.
        volumePath, detectionPath = case.find_files(directory)
        if volumePath is None:
            # Otherwise choosing the wrong folder is silent, which looks like a broken module.
            slicer.util.errorDisplay(case.describe_no_volume(directory))
            return

        self.reset()

        # str: Qt cannot convert a Path, and the loader hands the name straight to it.
        # The returned node is the one just loaded; looking it up by class afterwards would
        # take whichever volume happens to be first in the scene.
        self.volNode = slicer.util.loadVolume(str(volumePath))
        # volume renderings view
        volRenLogic = slicer.modules.volumerendering.logic()
        displayNode = volRenLogic.GetFirstVolumeRenderingDisplayNode(self.volNode)
        if not displayNode:
            displayNode = volRenLogic.CreateDefaultVolumeRenderingNodes(self.volNode)
        volRenLogic.UpdateDisplayNodeFromVolumeNode(displayNode, self.volNode)

        self.volProp = displayNode.GetVolumePropertyNode().GetVolumeProperty()
        self.renderingRoiNode = displayNode.GetROINode()

        # define the scalarOpacity function. Each of these three assignments emits
        # valueChanged, so unblocked they rebuild the ramp against a range that is
        # only half updated; the explicit call afterwards is the one that counts.
        min_val, max_val = self.volNode.GetImageData().GetScalarRange()
        wasBlocked = self.ui.volumeThreshold.blockSignals(True)
        self.ui.volumeThreshold.maximum = max_val
        self.ui.volumeThreshold.minimum = min_val
        self.ui.volumeThreshold.value = (max_val + min_val) / 3
        self.ui.volumeThreshold.blockSignals(wasBlocked)
        self.updateScalarOpacity()

        self.ui.vol_visibility.checked = 1
        # The colour and crop the reviewer set for the previous case are still on screen, and
        # apply to this one too; otherwise they show in the panel but not in the render.
        self.set_volume_color()
        self.onCropChanged()

        # detections
        if detectionPath is None:
            # The volume is worth looking at on its own, so this warns rather than refusing —
            # but with no detection file there is no review, and nothing is being recorded.
            slicer.util.warningDisplay(case.describe_no_detections(directory))
            return
        if not self.load_detections(detectionPath):
            return
        if not self.startReview(directory, volumePath, detectionPath):
            self.reset()
            return
        self.create_spheres()
        self.fillReviewTable()

    def load_detections(self, file_path):
        # Parsing and validation live in ReformatVizLib; this method only turns a failure
        # into something the reviewer sees. DetectionFormatError and json's decode error are
        # both ValueError.
        try:
            self.detections = det.load(file_path)
        except (OSError, ValueError) as e:
            slicer.util.errorDisplay(det.describe_load_failure(file_path, e))
            self.detections = []
            return False
        return True

    def startReview(self, directory, volumePath, detectionPath):
        """Open the case's review, resuming the one on disk if there is one.

        Returns False if the case must not be reviewed at all, which is the answer whenever
        an existing review cannot be read: opening the case would start a second review over
        the top of a record nobody has been able to look at.
        """
        self.reviewPath = directory / rev.file_name(reviewerName())
        digest = jsonio.sha256(detectionPath)

        if self.reviewPath.exists():
            try:
                self.review = rev.load(self.reviewPath)
            except (OSError, ValueError) as e:
                slicer.util.errorDisplay(rev.describe_load_failure(self.reviewPath, e))
                self.review = None
                self.reviewPath = None
                return False
            if not rev.matches_source(self.review, digest):
                slicer.util.warningDisplay(rev.describe_source_mismatch(self.review))
            self.restoreAddedDetections()
            if self.review["review"]["state"] != rev.SIGNED_OFF:
                rev.add_candidates(self.review, [d[det.SOURCE_KEY] for d in self.detections])
            self.restoreViewAngles()
            return True

        self.review = rev.new(
            study={"directory": directory.name},
            source={"detectionFile": detectionPath.name, "sha256": digest, "volumeFile": volumePath.name},
            reviewer=reviewerName(),
            tool=toolDescription(),
            at=now(),
            keys=[d[det.SOURCE_KEY] for d in self.detections],
        )
        self.saveReview()
        return True

    def restoreAddedDetections(self):
        """Put the findings the reviewer added in an earlier session back on the list.

        They exist only in the review — the detection file is the detector's and is never
        written to — so without this a resumed case silently loses the reviewer's own
        findings.
        """
        for key in rev.added_keys(self.review):
            entry = rev.entry(self.review, key)
            detection = det.from_points(entry["P1"], entry["P2"])
            detection[det.SOURCE_KEY] = key
            self.detections.append(detection)

    def restoreViewAngles(self):
        """Reopen each candidate at the angle the reviewer last looked at it from."""
        for d in self.detections:
            entry = self.reviewEntry(d)
            if entry:
                d["viewAngle"] = entry["viewAngle"]

    def reviewEntry(self, detection):
        """Return the review entry for ``detection``, or None when no case is open."""
        if not self.review:
            return None
        return self.review["detections"].get(detection.get(det.SOURCE_KEY))

    def saveReview(self):
        """Write the review out. Called after every change the reviewer makes.

        The reviewer never asks for this: a verdict that is only in the module is a verdict
        lost to the next crash. A failure is reported once rather than on every keystroke,
        because a dialog per character would be its own kind of silence.
        """
        if not self.review or not self.reviewPath:
            return
        try:
            rev.save(self.reviewPath, self.review)
        except OSError as e:
            if not self.autosaveFailed:
                self.autosaveFailed = True
                slicer.util.errorDisplay(rev.describe_save_failure(self.reviewPath, e))
            return
        self.autosaveFailed = False
        self.angleUnsaved = False

    def saveViewAngle(self):
        """Write the review out if the only change since the last save is a view angle."""
        if self.angleUnsaved:
            self.saveReview()

    def applyToReview(self, change):
        """Make one change to the review, then write it out and refresh the progress."""
        if not self.review:
            return False
        try:
            change()
        except rev.ReviewError as e:
            slicer.util.errorDisplay(str(e))
            return False
        self.saveReview()
        self.updateProgress()
        return True

    def updateProgress(self):
        """Show how much of the case is decided, next to the table it refers to."""
        self.updateAnnotationButtons()
        if not self.review:
            self.ui.CollapsibleButton.text = self.reviewSectionTitle
            self.ui.signOffButton.text = "Sign off"
            return
        self.ui.CollapsibleButton.text = f"{self.reviewSectionTitle} \u2014 {rev.describe_progress(self.review)}"
        signedOff = self.review["review"]["state"] == rev.SIGNED_OFF
        self.ui.signOffButton.text = "Reopen" if signedOff else "Sign off"

    def reviewIsEditable(self):
        """False only while a signed-off case is open; with no case there is nothing to lock."""
        return not self.review or self.review["review"]["state"] != rev.SIGNED_OFF

    def updateAnnotationButtons(self):
        """Offer Add when nothing is being placed and the case accepts findings; OK and
        Cancel only while something is."""
        placing = self.tmpMark is not None
        self.ui.addButton.setEnabled(not placing and self.reviewIsEditable())
        self.ui.okButton.setEnabled(placing)
        self.ui.cancelButton.setEnabled(placing)

    def fillReviewTable(self):
        """Show the open review: one row per detection, locked if the case is finished.

        The lock matters on the way in, not only after `onSignOff`: a case signed off in an
        earlier session is loaded from disk already finished, and without this its controls
        look editable and answer the first change with an error dialog.
        """
        for d in self.detections:
            self.add_detection_to_table(d)
        # Once the rows exist, not once per row.
        self.tableWidget.setCurrentItem(self.tableWidget.item(self.selected_detection, COL_CONFIDENCE))
        self.updateProgress()
        if self.review:
            self.setReviewEditable(self.review["review"]["state"] != rev.SIGNED_OFF)

    # index is necessary because the table is not updated immediately after an insertion, so one cannot rely on rowCount
    def add_detection_to_table(self, d):
        idx = self.tableWidget.rowCount
        # No key and no entry when no case is open; the handlers then have nothing to record
        # to, which applyToReview already refuses.
        key = d.get(det.SOURCE_KEY)
        entry = self.reviewEntry(d) or {}
        # The cell widgets below emit as they are populated; those signals would record the
        # defaults back over the verdicts just restored from disk.
        self.populatingTable = True
        self.tableWidget.insertRow(idx)
        # needed here because columns headers require at least one row...
        self.tableWidget.setColumnCount(len(TABLE_COLUMNS))
        self.tableWidget.setHorizontalHeaderLabels(TABLE_COLUMNS)

        # Confidence score
        itm = qt.QTableWidgetItem(str(det.confidence_percent(d)))
        itm.setTextAlignment(4)
        self.tableWidget.setItem(idx, COL_CONFIDENCE, itm)

        # Diameter (mm)
        itm = qt.QTableWidgetItem(str(det.diameter(d)))
        itm.setTextAlignment(4)
        self.tableWidget.setItem(idx, COL_DIAMETER, itm)

        # Verdict
        verdict = qt.QComboBox()
        for label, _ in table.VERDICT_CHOICES:
            verdict.addItem(label)
        state = entry.get("state", rev.UNREVIEWED)
        verdict.setCurrentIndex(table.choice_index(table.VERDICT_CHOICES, state))
        self.tableWidget.setCellWidget(idx, COL_VERDICT, verdict)
        verdict.currentIndexChanged.connect(functools.partial(self.onVerdictChanged, key=key, row=idx))

        # Why the candidate was rejected
        reason = qt.QComboBox()
        for label, _ in table.REASON_CHOICES:
            reason.addItem(label)
        reason.setCurrentIndex(table.choice_index(table.REASON_CHOICES, entry.get("reason")))
        reason.setEnabled(table.reason_enabled(state))
        self.tableWidget.setCellWidget(idx, COL_REASON, reason)
        reason.currentIndexChanged.connect(functools.partial(self.onReasonChanged, key=key, row=idx))

        # Likert score
        lv = qt.QComboBox()
        for label, _ in table.LIKERT_CHOICES:
            lv.addItem(label)
        lv.setCurrentIndex(table.choice_index(table.LIKERT_CHOICES, entry.get("likert")))
        self.tableWidget.setCellWidget(idx, COL_LIKERT, lv)
        lv.currentIndexChanged.connect(functools.partial(self.onLikertChanged, key=key, row=idx))

        # Note
        self.tableWidget.setItem(idx, COL_NOTE, qt.QTableWidgetItem(entry.get("note", "")))

        self.populatingTable = False

    def onVerdictChanged(self, index, key, row):
        if self.populatingTable:
            return
        state = table.VERDICT_CHOICES[index][1]
        reasonCombo = self.tableWidget.cellWidget(row, COL_REASON)
        reasonCombo.setEnabled(table.reason_enabled(state))
        reason = table.REASON_CHOICES[reasonCombo.currentIndex][1] if state == rev.REJECTED else None
        if not self.applyToReview(functools.partial(rev.set_state, self.review, key, state, now(), reason)):
            self.showRecordedRow(row, key)
            return
        if state != rev.REJECTED and reasonCombo.currentIndex != 0:
            # The reason no longer applies; the record has already dropped it.
            wasBlocked = reasonCombo.blockSignals(True)
            reasonCombo.setCurrentIndex(0)
            reasonCombo.blockSignals(wasBlocked)

    def onReasonChanged(self, index, key, row):
        if self.populatingTable:
            return
        state = table.VERDICT_CHOICES[self.tableWidget.cellWidget(row, COL_VERDICT).currentIndex][1]
        reason = table.REASON_CHOICES[index][1]
        if not self.applyToReview(functools.partial(rev.set_state, self.review, key, state, now(), reason)):
            self.showRecordedRow(row, key)

    def onLikertChanged(self, index, key, row):
        if self.populatingTable:
            return
        score = table.LIKERT_CHOICES[index][1]
        if not self.applyToReview(functools.partial(rev.set_likert, self.review, key, score, now())):
            self.showRecordedRow(row, key)

    def showRecordedRow(self, row, key):
        """Put a row's combos back to what the review records, after a refused change.

        Otherwise the combo keeps showing the value the reviewer picked, which the review
        never took, and the table and the file disagree until the case is reopened.
        """
        entry = (self.review or {}).get("detections", {}).get(key) or {}
        state = entry.get("state", rev.UNREVIEWED)
        editable = not self.review or self.review["review"]["state"] != rev.SIGNED_OFF
        self.populatingTable = True
        self.tableWidget.cellWidget(row, COL_VERDICT).setCurrentIndex(table.choice_index(table.VERDICT_CHOICES, state))
        reason = self.tableWidget.cellWidget(row, COL_REASON)
        reason.setCurrentIndex(table.choice_index(table.REASON_CHOICES, entry.get("reason")))
        reason.setEnabled(table.reason_enabled(state, editable))
        self.tableWidget.cellWidget(row, COL_LIKERT).setCurrentIndex(
            table.choice_index(table.LIKERT_CHOICES, entry.get("likert"))
        )
        self.populatingTable = False

    def onNoteEdited(self, item):
        if self.populatingTable or item.column() != COL_NOTE:
            return
        # No key when no case is open — an annotation placed against a bare volume has
        # nowhere to be recorded. Read it the way the combo handlers do rather than
        # subscripting, which raises before applyToReview can decline.
        key = self.detections[item.row()].get(det.SOURCE_KEY)
        if key is None:
            return
        self.applyToReview(functools.partial(rev.set_note, self.review, key, item.text(), now()))

    def onSignOff(self):
        """Finish the case, or reopen a finished one.

        Sign-off is what tells whatever reads the review that it is looking at a complete
        read, so it is refused while any candidate is undecided — and reopening is a recorded
        amendment rather than a silent edit.
        """
        if not self.review:
            slicer.util.warningDisplay("Open a case first: there is no review to sign off.")
            return

        if self.review["review"]["state"] == rev.SIGNED_OFF:
            reason = qt.QInputDialog.getText(None, "Reopen this case", "Why is the review being amended?")
            if reason:
                self.reopenCase(reason)
            return

        if not self.applyToReview(functools.partial(rev.sign_off, self.review, now())):
            return
        self.setReviewEditable(False)
        slicer.util.infoDisplay(f"Case signed off by {self.review['review']['reviewer']}.")

    def reopenCase(self, reason):
        """Reopen a signed-off case for amendment, recording ``reason``."""
        if not self.applyToReview(functools.partial(rev.reopen, self.review, now(), reason)):
            return
        # Candidates the detection file gained while the case was signed off could not enter
        # the review then; without this they stay rows that refuse every verdict.
        if rev.add_candidates(self.review, [d[det.SOURCE_KEY] for d in self.detections]):
            self.saveReview()
            self.updateProgress()
        self.setReviewEditable(True)

    def setReviewEditable(self, editable):
        """Lock the verdicts of a signed-off case, or unlock them after a reopen."""
        # Changing an item's flags emits itemChanged, which onNoteEdited would record as a
        # note: an event per row on every open, and a refused one per row on sign-off.
        self.populatingTable = True
        for row in range(self.tableWidget.rowCount):
            for column in (COL_VERDICT, COL_LIKERT):
                widget = self.tableWidget.cellWidget(row, column)
                if widget:
                    widget.setEnabled(editable)
            reason = self.tableWidget.cellWidget(row, COL_REASON)
            if reason:
                reason.setEnabled(table.reason_enabled(self._rowState(row), editable))
            note = self.tableWidget.item(row, COL_NOTE)
            if note:
                if editable:
                    note.setFlags(note.flags() | qt.Qt.ItemIsEditable)
                else:
                    note.setFlags(note.flags() & ~qt.Qt.ItemIsEditable)
        self.populatingTable = False

    def _rowState(self, row):
        verdict = self.tableWidget.cellWidget(row, COL_VERDICT)
        return table.VERDICT_CHOICES[verdict.currentIndex][1] if verdict else None

    def onCurrentCellChanged(self, row, column, previousRow, previousColumn):
        if self.populatingTable or row == self.selected_detection:
            return
        self.onRowSelected(row)

    def onRowClicked(self, index):
        # The click that changed the current cell has already been handled by
        # onCurrentCellChanged; doing it again would reformat and save twice.
        if index.row() != self.selected_detection:
            self.onRowSelected(index.row())

    def onRowSelected(self, item):
        self.selected_detection = item.row() if not isinstance(item, int) else item

        # Leaving a row is where the angle it was left at reaches the disk: the slider itself
        # changes it far too often to write out every time.
        self.saveViewAngle()
        self.updateVisibility()
        self.prepare_slices()

    def unselectAllRows(self, reset_view=True):
        self.tableWidget.clearSelection()
        self.selected_detection = -1
        self.updateVisibility()
        if reset_view:
            self.prepare_slices()

    def create_spheres(self):
        # One scene update for the whole case rather than one per node change: unbatched,
        # every model, display node and control point added was processed on its own, which
        # cost about 50 ms per candidate.
        self.ensureMarkupsNodes()
        markups = (self.confidences, self.bases, self.tips)
        slicer.mrmlScene.StartState(slicer.mrmlScene.BatchProcessState)
        # The markups nodes too: each control point added is otherwise its own modification.
        wereModifying = [node.StartModify() for node in markups]
        try:
            for d in self.detections:
                self.add_detection_to_scene(d)
            self.updateObjectsDisplay()
        finally:
            for node, wasModifying in zip(markups, wereModifying, strict=True):
                node.EndModify(wasModifying)
            slicer.mrmlScene.EndState(slicer.mrmlScene.BatchProcessState)

    def updateObjectsDisplay(self):
        self.set_lines_color()
        self.set_spheres_color()
        self.set_spheres_opacity()
        self.set_markups_color()
        self.set_confidence_color()
        self.updateVisibility()

    def updateVisibility(self):
        """Show the selected candidate's annotations, or every candidate's with none selected.

        The only part of the display a change of selection affects; colours and opacity
        follow their own controls.
        """
        self.set_lines_visibility()
        self.set_spheres_visibility()
        self.set_markups_visibility()
        self.set_confidence_visibility()

    def ensureMarkupsNodes(self):
        """Create the three shared markups nodes, or replace them if they left the scene.

        Closing the scene from the File menu removes them while this widget keeps its
        references, and control points added to a node that is no longer in the scene go
        nowhere: the bases, tips and confidence labels silently stop appearing.
        """
        for attribute, name in (("bases", "Base"), ("tips", "Tip"), ("confidences", "Confidences")):
            node = getattr(self, attribute, None)
            if node is None or not slicer.mrmlScene.IsNodePresent(node):
                node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
                node.SetName(name)
                node.SetLocked(True)
                setattr(self, attribute, node)
            if not node.GetDisplayNode():
                node.CreateDefaultDisplayNodes()

    def add_detection_to_scene(self, d):
        # Sphere
        sphere = createSphere(d["center"], d["radius"])

        # Assign a unique name to each sphere
        idx = len(self.spheres)
        m = createModel(sphere, name=f"Sphere_{idx}")
        self.spheres.append(m)

        # Line
        line = vtk.vtkLineSource()
        line.SetPoint1(d["P1"])
        line.SetPoint2(d["P2"])
        line.SetResolution(30)
        line.Update()
        m = createModel(line, name=f"Line_{idx}")
        self.lines.append(m)

        self.ensureMarkupsNodes()

        # Confidence score label, the base and the tip: the Nth control point of each
        # belongs to detection N.
        n = self.confidences.AddControlPoint(d["center"])
        self.confidences.SetNthControlPointLabel(n, str(det.confidence_percent(d)) + " %")
        self.bases.AddControlPoint(d["P1"])
        self.tips.AddControlPoint(d["P2"])

    def set_spheres_visibility(self):
        for i in range(len(self.detections)):
            self.spheres[i].GetDisplayNode().SetVisibility(
                rendering.is_visible(i, self.selected_detection, self.ui.SpheresVisibility.checked)
            )

    def set_spheres_color(self, color=None):
        color = rendering.to_rgb(color if color is not None else self.ui.SpheresColorPicker.color)
        for i in range(len(self.detections)):
            self.spheres[i].GetDisplayNode().SetColor(color)

    def set_spheres_opacity(self):
        for i in range(len(self.detections)):
            self.spheres[i].GetDisplayNode().SetOpacity(self.ui.SpheresOpacity.value)

    def set_lines_color(self, color=None):
        color = rendering.to_rgb(color if color is not None else self.ui.LinesColorPicker.color)
        for i in range(len(self.detections)):
            self.lines[i].GetDisplayNode().SetColor(color)

    def set_lines_visibility(self):
        for i in range(len(self.detections)):
            self.lines[i].GetDisplayNode().SetVisibility(
                rendering.is_visible(i, self.selected_detection, self.ui.LinesVisibility.checked)
            )

    def set_confidence_color(self, color=None):
        if self.confidences.GetDisplayNode():
            color = rendering.to_rgb(color if color is not None else self.ui.ConfidenceColorPicker.color)
            self.confidences.GetDisplayNode().SetColor(color)
            self.confidences.GetDisplayNode().SetSelectedColor(color)

    def set_confidence_visibility(self):
        if self.confidences:
            for i in range(len(self.detections)):
                self.confidences.SetNthControlPointVisibility(
                    i, rendering.is_visible(i, self.selected_detection, self.ui.ConfidenceVisibility.checked)
                )

    def set_markups_color(self, color=None):
        if self.bases.GetDisplayNode() and self.tips.GetDisplayNode():
            color = rendering.to_rgb(color if color is not None else self.ui.MarkupsColorPicker.color)
            for markups in (self.bases, self.tips):
                markups.GetDisplayNode().SetColor(color)
                markups.GetDisplayNode().SetSelectedColor(color)

    def set_markups_visibility(self):
        for i in range(len(self.detections)):
            value = rendering.is_visible(i, self.selected_detection, self.ui.MarkupsVisibility.checked)
            self.bases.SetNthControlPointVisibility(i, value)
            self.tips.SetNthControlPointVisibility(i, value)

    def prepare_slices(self):
        if len(self.detections) == 0 or self.selected_detection < 0:
            self.candidateAxis, self.candidateOrigin, self.candidateRz = geometry.default_frame()
            angleValue = 0  # need to update slices
        else:
            d = self.detections[self.selected_detection]
            self.candidateAxis, self.candidateOrigin, self.candidateRz = geometry.candidate_frame(d["P1"], d["P2"])
            angleValue = d["viewAngle"]

        self.reformatLogic = slicer.modules.reformat.logic()

        # Setting the value emits valueChanged, which reformats; the explicit call below has
        # to stay, because switching between two detections saved at the same angle emits
        # nothing while still needing the new frame applied. Blocking the signal keeps that
        # from reformatting the Red slice twice on every row click.
        wasBlocked = self.ui.rotateAxisSlider.blockSignals(True)
        self.ui.rotateAxisSlider.value = angleValue
        self.ui.rotateAxisSlider.blockSignals(wasBlocked)
        self.rotateAxisSlider(self.ui.rotateAxisSlider.value)
        self.thicknessSlider(self.ui.thicknessSlider.value)
        self.centerCamera()
        self.onCropChanged()

    def centerCamera(self):
        # camera focal point
        layoutManager = slicer.app.layoutManager()
        threeDWidget = layoutManager.threeDWidget(0) if layoutManager else None
        if threeDWidget is None:
            return
        camera = (
            threeDWidget.threeDView()
            .displayableManagerByClassName("vtkMRMLCameraDisplayableManager")
            .GetCameraWidget()
            .GetCameraNode()
        )

        sliceNode = slicer.mrmlScene.GetNodeByID("vtkMRMLSliceNodeRed")
        if self.selected_detection < 0 or sliceNode is None:
            upaxis = np.array([0, 0, 1])
            normal = np.array([0, -1, 0])
            origin = np.array([0, 0, 0])
        else:
            m = sliceNode.GetSliceToRAS()
            upaxis = np.array(m.MultiplyDoublePoint([0, 1, 0, 0]))[:3]
            normal = np.array(m.MultiplyDoublePoint([0, 0, 1, 0]))[:3]
            origin = np.array(m.MultiplyDoublePoint([0, 0, 0, 1]))[:3]

        # keep the current distance, only move the camera around the target
        distance = np.linalg.norm(np.array(camera.GetFocalPoint()) - np.array(camera.GetPosition()))

        camera.SetFocalPoint(origin.tolist())
        camera.SetPosition(geometry.camera_position(origin, normal, distance).tolist())
        camera.SetViewUp(upaxis.tolist())

    def set_volume_visibility(self):
        displayNode = self.volumeRenderingDisplayNode()
        if displayNode:
            displayNode.SetVisibility(self.ui.vol_visibility.checked)

    def set_volume_translucent(self):
        self.updateScalarOpacity()

    def prepareAnnotationNode(self, name="P"):
        self.removeAnnotationNodes()

        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", name)
        node.CreateDefaultDisplayNodes()

        node.AddObserver(slicer.vtkMRMLMarkupsNode.PointPositionDefinedEvent, self.drawAnnotationSpheres)
        node.AddObserver(slicer.vtkMRMLMarkupsNode.PointModifiedEvent, self.MoveSpheres)

        self.tmpMark = node

    def addAnnotationNode(self):
        self.unselectAllRows(reset_view=False)
        if not self.tmpMark:
            self.prepareAnnotationNode(name="P")

        slicer.modules.markups.logic().SetActiveList(self.tmpMark)
        slicer.modules.markups.logic().StartPlaceMode(1)

        self.updateAnnotationButtons()

    def removeAnnotationNodes(self, caller=None, event=None):
        if self.tmpMark:
            self.stopPlacing()
        # Present-checked: after a scene close both are already gone.
        for node in (self.tmpMark, self.tmpSphere):
            if node is not None and slicer.mrmlScene.IsNodePresent(node):
                slicer.mrmlScene.RemoveNode(node)

        self.tmpMark: slicer.vtkMRMLMarkupsFiducialNode = None
        self.tmpSphere: slicer.vtkMRMLModelNode = None
        self.tmpSphereSource: slicer.vtkSphereSource = None

        self.updateAnnotationButtons()

    def validateAnnotation(self):
        markups = self.tmpMark
        if not markups or markups.GetNumberOfControlPoints() != 2:
            # Otherwise OK looks broken: nothing happens and Add stays greyed out, with no
            # indication that the base and the tip both have to be placed first.
            slicer.util.warningDisplay(
                "Place two points — the base, then the tip — before pressing OK, or press Cancel to start over."
            )
            return

        if not self.reviewIsEditable():
            # Signed off while the points were being placed. Refused before anything is
            # removed, so the points survive a reopen instead of being lost to the error.
            slicer.util.warningDisplay("The case is signed off. Reopen it to add a finding, or press Cancel.")
            return

        p1, p2 = np.empty(3), np.empty(3)
        markups.GetNthControlPointPositionWorld(0, p1)
        markups.GetNthControlPointPositionWorld(1, p2)
        self.removeAnnotationNodes()

        detection = det.from_points(p1, p2)
        if self.review:
            # Its key comes from the review, which owns the reviewer's own findings: the
            # detection file is the detector's and is never written to.
            key = rev.add_detection(self.review, detection["P1"], detection["P2"], now())
            detection[det.SOURCE_KEY] = key
            self.saveReview()
        self.detections.append(detection)
        self.add_detection_to_table(detection)
        self.add_detection_to_scene(detection)
        self.selected_detection = self.tableWidget.rowCount - 1
        self.tableWidget.setCurrentItem(self.tableWidget.item(self.selected_detection, 0))
        self.onRowSelected(self.selected_detection)
        self.updateObjectsDisplay()
        self.updateAnnotationButtons()

    def stopPlacing(self):
        """Leave place mode, so the next click in a view moves the view again.

        markups.logic().StartPlaceMode(0) only makes placement non-persistent; it leaves the
        mode on, and the next click lands a point in whatever node is active by then.
        """
        interaction = slicer.app.applicationLogic().GetInteractionNode()
        if interaction and interaction.GetCurrentInteractionMode() == interaction.Place:
            interaction.SwitchToViewTransformMode()

    def drawAnnotationSpheres(self, caller=None, event=None):
        markups: slicer.vtkMRMLMarkupsFiducialNode = caller
        nb_points = markups.GetNumberOfControlPoints()
        if nb_points < 2:
            return

        self.stopPlacing()
        markups.GetDisplayNode().SetSnapMode(slicer.vtkMRMLMarkupsDisplayNode.SnapModeUnconstrained)
        if self.tmpSphere is not None:
            # A point beyond the second: the preview already exists, and a second model here
            # would be left behind by Cancel and OK alike.
            self.MoveSpheres(markups)
            return
        p1, p2 = np.empty(3), np.empty(3)
        markups.GetNthControlPointPositionWorld(0, p1)
        markups.GetNthControlPointPositionWorld(1, p2)

        detection = det.from_points(p1, p2)
        self.tmpSphereSource = createSphere(detection["center"], detection["radius"])
        self.tmpSphere = createModel(self.tmpSphereSource, name="Tmp Sphere")

        self.tmpSphere.GetDisplayNode().SetVisibility(True)
        color = rendering.to_rgb(self.ui.SpheresColorPicker.color)
        self.tmpSphere.GetDisplayNode().SetColor(color)
        self.tmpSphere.GetDisplayNode().SetOpacity(self.ui.SpheresOpacity.value)

    def MoveSpheres(self, caller=None, event=None):
        markups: slicer.vtkMRMLMarkupsFiducial = caller

        # PointModifiedEvent still fires after a point is deleted, and reading a control
        # point that no longer exists leaves the np.empty buffer holding whatever was in that
        # memory, which then becomes the sphere's centre and radius.
        if self.tmpSphereSource and markups.GetNumberOfControlPoints() >= 2:
            p1, p2 = np.empty(3), np.empty(3)
            markups.GetNthControlPointPositionWorld(0, p1)
            markups.GetNthControlPointPositionWorld(1, p2)
            detection = det.from_points(p1, p2)
            self.tmpSphereSource.SetCenter(detection["center"])
            self.tmpSphereSource.SetRadius(detection["radius"])
            # No forced render: the preview model observes this source, so Update() already
            # asks the views to redraw at their own rate. Forcing it rendered every view
            # synchronously on each mouse move of the drag.
            self.tmpSphereSource.Update()

    def reset(self):
        # Batched for the same reason as create_spheres: about 60 ms per candidate otherwise.
        slicer.mrmlScene.StartState(slicer.mrmlScene.BatchProcessState)
        try:
            self.removeCaseNodes()
        finally:
            slicer.mrmlScene.EndState(slicer.mrmlScene.BatchProcessState)
        self.removeAnnotationNodes()
        self.forgetSceneState()

    def removeCaseNodes(self):
        """Remove what this module added to the scene for the open case, and nothing else."""
        for node in self.spheres:
            slicer.mrmlScene.RemoveNode(node)
        for node in self.lines:
            slicer.mrmlScene.RemoveNode(node)
        self.confidences.RemoveAllControlPoints()
        self.bases.RemoveAllControlPoints()
        self.tips.RemoveAllControlPoints()
        if self.volNode:
            # The rendering display node and its volume property node outlive the volume, so
            # every load/Clean cycle left a pair of them behind. volProp belongs to the
            # property node: left set, the opacity sliders keep writing into a property whose
            # node has been removed.
            displayNode = self.volumeRenderingDisplayNode()
            if displayNode:
                propertyNode = displayNode.GetVolumePropertyNode()
                slicer.mrmlScene.RemoveNode(displayNode)
                if propertyNode:
                    slicer.mrmlScene.RemoveNode(propertyNode)
            slicer.mrmlScene.RemoveNode(self.volNode)
        # The ROI the rendering nodes came with: once crop swaps in the module's own, nothing
        # refers to it, and every case opened left one behind.
        if self.renderingRoiNode is not None and slicer.mrmlScene.IsNodePresent(self.renderingRoiNode):
            slicer.mrmlScene.RemoveNode(self.renderingRoiNode)
        # Ours only. Removing every ROI in the scene took the reviewer's own along with it.
        if self.currentRoiNode():
            slicer.mrmlScene.RemoveNode(self.roiNode)

    def forgetSceneState(self):
        """Bring the widget's own state back in line with an empty scene.

        Removes nothing from the scene: this is the tail of `reset()`, and it is also the
        whole of what a scene closed from the File menu needs, where the nodes have already
        been removed for us.
        """
        # The view angle only reaches disk when the reviewer leaves a row, because the slider
        # changes it far too often to write on every move — so the row they were on when they
        # pressed Clean, closed the scene or opened the next case has yet to be written. A
        # review that failed to load is None here, and is left exactly as it is on disk.
        self.saveViewAngle()

        self.spheres = []
        self.lines = []
        self.detections = []
        self.volNode: slicer.vtkMRMLScalarVolumeNode = None
        self.volProp = None
        self.roiNode = None
        self.renderingRoiNode = None

        # The review file itself stays on disk: it is the record of what was decided, and
        # Clean means "clear the screen", not "unsay it".
        self.review = None
        self.reviewPath = None
        self.autosaveFailed = False
        self.angleUnsaved = False

        self.populatingTable = True
        self.tableWidget.setRowCount(0)
        self.tableWidget.setCurrentItem(None)
        self.populatingTable = False
        self.updateProgress()
        self.onRowSelected(-1)

    def cleanup(self):
        """
        Called when the application closes and the module widget is destroyed.
        """
        # Before removing anything, so that onSceneEndClose does not run against a widget
        # that is on its way out.
        self.removeObservers()
        # Only what this module put in the scene. Emptying the whole scene here would take
        # the reviewer's other data with it every time the module was reloaded, and reset()
        # writes the review out on the way past.
        self.reset()
        # The three shared markups nodes outlive a case but not the widget: a reload builds a
        # new widget, whose ensureMarkupsNodes would add three more and leave these orphaned.
        for node in (self.confidences, self.bases, self.tips):
            if node is not None and slicer.mrmlScene.IsNodePresent(node):
                slicer.mrmlScene.RemoveNode(node)

    def onSceneEndClose(self, caller, event):
        """
        Called just after the scene is closed.
        """
        # The scene has taken every node this widget owned with it, so there is nothing left
        # to remove — but self.detections, the spheres, the lines and the table rows still
        # describe the closed scene, and the reviewer would go on scoring rows whose geometry
        # is gone. The three shared markups nodes are recreated because the widget keeps them
        # for the whole session rather than per patient.
        self.ensureMarkupsNodes()
        # A placement under way when the scene closed: its node went with the scene, and OK
        # would otherwise still turn it into a detection in an empty scene.
        self.removeAnnotationNodes()
        self.forgetSceneState()


class ReformatVizTest(ScriptedLoadableModuleTest):
    """The test `slicer_add_python_unittest` registers, and the module's Self Test button.

    This covers only what needs Slicer: that the module loads, that a detection file becomes
    scene nodes, that selecting a row reformats the Red slice, that two placed points become a
    detection, that the review is recorded and resumed, and that closing the scene resets the
    widget. The geometry and file format are covered far more thoroughly by the Slicer-free
    suite in `tests/`, which is where new cases belong unless they genuinely need MRML —
    which file in a directory is the detection file, for instance, is `tests/test_case.py`.
    """

    def setUp(self):
        slicer.mrmlScene.Clear()

    def runTest(self):
        self.setUp()
        self.test_module_loads()
        self.test_detections_become_scene_nodes()
        self.test_selecting_a_row_aligns_the_red_slice()
        self.test_a_hand_placed_annotation_becomes_a_detection()
        self.test_a_verdict_reaches_the_review_file_without_being_asked_to()
        self.test_a_case_cannot_be_signed_off_until_every_candidate_is_decided()
        self.test_a_case_is_resumed_exactly_as_it_was_left()
        self.test_a_case_whose_review_cannot_be_read_is_not_opened()
        self.test_a_signed_off_case_reopens_locked()
        self.test_locking_the_table_records_no_notes()
        self.test_the_arrow_keys_move_the_view_with_the_highlight()
        self.test_a_refused_verdict_is_not_left_on_screen()
        self.test_a_signed_off_case_takes_no_new_finding()
        self.test_reopening_takes_in_candidates_added_since_sign_off()
        self.test_placing_leaves_place_mode_and_one_preview()
        self.test_closing_the_scene_mid_placement_ends_it()
        self.test_opening_and_closing_cases_leaves_no_roi_behind()
        self.test_closing_without_volume_rendering_still_saves_the_angle()
        self.test_a_new_case_takes_the_colour_and_crop_on_screen()
        self.test_moving_between_rows_rewrites_the_review_only_for_a_new_angle()
        self.test_the_last_view_angle_survives_clean()
        self.test_a_note_on_a_candidate_with_no_case_open_is_ignored()
        self.test_the_volume_colour_picker_survives_missing_rendering_nodes()
        self.test_the_data_probe_is_hidden_while_the_module_is_open()
        self.test_a_case_opens_without_disturbing_the_rest_of_the_scene()
        self.test_closing_the_scene_resets_the_widget()

    def _widget(self):
        slicer.util.selectModule("ReformatViz")
        return slicer.modules.ReformatVizWidget

    def _samplePath(self):
        # Ships with the source tree, not with the installed extension.
        return Path(__file__).parents[1] / "tests" / "data" / "detections_sample.json"

    def _addTestVolume(self):
        """Add a synthetic 1 mm volume large enough to contain every sample detection.

        The reformatting cannot be checked in an empty scene: with no background volume the
        slice logic leaves the Red slice at the world origin whatever origin is asked for.
        Building the volume here keeps the test free of any download.
        """
        ijkToRAS = np.eye(4)
        ijkToRAS[:3, 3] = [-64.0, -64.0, -64.0]
        array = np.zeros((128, 128, 128), dtype=np.int16)
        array[32:96, 32:96, 32:96] = 400
        volume = slicer.util.addVolumeFromArray(array, ijkToRAS=ijkToRAS, name="ReformatVizTestVolume")
        slicer.util.setSliceViewerLayers(background=volume, fit=True)
        return volume

    def test_a_hand_placed_annotation_becomes_a_detection(self):
        self.delayDisplay("Two placed points become a detection the reviewer can score")
        widget = self._widget()
        widget.reset()
        widget.volNode = self._addTestVolume()

        base, tip = [10.0, 0.0, 0.0], [10.0, 0.0, 6.0]
        widget.prepareAnnotationNode()
        widget.tmpMark.AddControlPoint(base)
        widget.tmpMark.AddControlPoint(tip)
        widget.validateAnnotation()

        self.assertEqual(len(widget.detections), 1)
        detection = widget.detections[0]
        # Placed by the reviewer, not by a detector: 'added' is what keeps it at the end of
        # the table after a save and reload, whatever its confidence.
        self.assertEqual(detection["added"], 1)
        self.assertEqual(detection["center"], [10.0, 0.0, 3.0])
        self.assertAlmostEqual(detection["radius"], 3.0)
        # The row, the sphere, the line and the three markups points all have to arrive
        # together, or the positional invariant between them is already broken.
        self.assertEqual(widget.tableWidget.rowCount, 1)
        self.assertEqual(len(widget.spheres), 1)
        self.assertEqual(len(widget.lines), 1)
        for markups in (widget.bases, widget.tips, widget.confidences):
            self.assertEqual(markups.GetNumberOfControlPoints(), 1)
        # The temporary placement node and its preview sphere are gone.
        self.assertIsNone(widget.tmpMark)
        self.assertIsNone(widget.tmpSphere)

        widget.reset()
        self.delayDisplay("Passed")

    def _buildCase(self):
        """Write a patient-shaped directory: one volume, one detection file.

        The same thing load_files finds behind the directory dialog, which the tests cannot
        open — so they call load_detections and startReview the way it does.
        """
        directory = Path(slicer.util.tempDirectory()) / "case"
        directory.mkdir()
        volume = self._addTestVolume()
        self.assertTrue(slicer.util.saveNode(volume, str(directory / "angio.nii.gz")))
        (directory / "detections.json").write_text(self._samplePath().read_text())
        return directory

    def _openCase(self, widget, directory):
        self.assertTrue(widget.load_detections(directory / "detections.json"))
        self.assertTrue(widget.startReview(directory, directory / "angio.nii.gz", directory / "detections.json"))
        widget.create_spheres()
        widget.fillReviewTable()

    def _reviewFile(self, directory):
        return directory / rev.file_name(reviewerName())

    def _setVerdict(self, widget, row, label):
        """Pick a verdict the way the reviewer does, through the combo's signal."""
        index = next(i for i, (text, _) in enumerate(table.VERDICT_CHOICES) if text == label)
        widget.tableWidget.cellWidget(row, COL_VERDICT).setCurrentIndex(index)

    def test_a_verdict_reaches_the_review_file_without_being_asked_to(self):
        self.delayDisplay("A verdict is recorded, with its reason, and autosaved")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._openCase(widget, directory)

        # The review exists from the moment the case is opened, with nothing decided.
        onDisk = rev.load(self._reviewFile(directory))
        self.assertEqual(len(rev.undecided_keys(onDisk)), 3)
        self.assertEqual(onDisk["source"]["detectionFile"], "detections.json")
        self.assertEqual(onDisk["review"]["reviewer"], reviewerName())

        key = widget.detections[0][det.SOURCE_KEY]
        self._setVerdict(widget, 0, "No")
        widget.tableWidget.cellWidget(0, COL_REASON).setCurrentIndex(1)  # infundibulum

        # No save was asked for: the file already says so.
        onDisk = rev.load(self._reviewFile(directory))
        self.assertEqual(rev.entry(onDisk, key)["state"], rev.REJECTED)
        self.assertEqual(rev.entry(onDisk, key)["reason"], "infundibulum")
        # And the candidates nobody looked at are still telling the truth about themselves.
        self.assertEqual(len(rev.undecided_keys(onDisk)), 2)

        widget.reset()
        self.delayDisplay("Passed")

    def test_a_case_cannot_be_signed_off_until_every_candidate_is_decided(self):
        self.delayDisplay("Sign-off waits for the last undecided candidate")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._openCase(widget, directory)

        self._setVerdict(widget, 0, "Yes")
        widget.onSignOff()  # refused: two candidates are still undecided
        self.assertEqual(rev.load(self._reviewFile(directory))["review"]["state"], rev.IN_REVIEW)

        self._setVerdict(widget, 1, "No")
        self._setVerdict(widget, 2, "Not assessable")
        widget.onSignOff()

        onDisk = rev.load(self._reviewFile(directory))
        self.assertEqual(onDisk["review"]["state"], rev.SIGNED_OFF)
        self.assertIsNotNone(onDisk["review"]["signedOffAt"])
        # A finished case does not accept more verdicts without a recorded reopen.
        self.assertFalse(widget.tableWidget.cellWidget(0, COL_VERDICT).enabled)

        widget.reset()
        self.delayDisplay("Passed")

    def test_a_case_is_resumed_exactly_as_it_was_left(self):
        self.delayDisplay("Reopening a case restores its verdicts and its own findings")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._openCase(widget, directory)

        key = widget.detections[0][det.SOURCE_KEY]
        self._setVerdict(widget, 0, "Yes")
        widget.tableWidget.cellWidget(0, COL_LIKERT).setCurrentIndex(4)
        widget.onRowSelected(0)
        widget.rotateAxisSlider(35)

        # A finding the detector missed, added the way the reviewer adds one.
        widget.prepareAnnotationNode()
        widget.tmpMark.AddControlPoint([10.0, 0.0, 0.0])
        widget.tmpMark.AddControlPoint([10.0, 0.0, 6.0])
        widget.validateAnnotation()
        addedKey = widget.detections[-1][det.SOURCE_KEY]

        # Close the case the way Clean does, then open it again.
        widget.reset()
        self.assertEqual(widget.detections, [])
        self._openCase(widget, directory)

        self.assertEqual(len(widget.detections), 4)
        self.assertEqual(rev.entry(widget.review, key)["state"], rev.CONFIRMED)
        self.assertEqual(rev.entry(widget.review, key)["likert"], 4)
        self.assertEqual(rev.entry(widget.review, key)["viewAngle"], 35)
        # The reviewer's own finding is back, with its verdict and its geometry.
        self.assertEqual(widget.detections[-1][det.SOURCE_KEY], addedKey)
        self.assertEqual(rev.entry(widget.review, addedKey)["state"], rev.CONFIRMED)
        self.assertEqual(widget.detections[-1]["P2"], [10.0, 0.0, 6.0])
        # ...including in the views and the table, not only in the record.
        self.assertEqual(len(widget.spheres), 4)
        self.assertEqual(widget.tableWidget.rowCount, 4)
        # And the table shows what was decided rather than a fresh set of defaults.
        self.assertEqual(
            widget.tableWidget.cellWidget(0, COL_VERDICT).currentIndex,
            [s for _, s in table.VERDICT_CHOICES].index(rev.CONFIRMED),
        )

        widget.reset()
        self.delayDisplay("Passed")

    def test_a_case_whose_review_cannot_be_read_is_not_opened(self):
        self.delayDisplay("An unreadable review is never overwritten")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._reviewFile(directory).write_text("{ this is not a review")

        self.assertTrue(widget.load_detections(directory / "detections.json"))
        self.assertFalse(widget.startReview(directory, directory / "angio.nii.gz", directory / "detections.json"))
        self.assertIsNone(widget.review)
        # Still exactly as it was: starting a second review over it would be the one
        # unrecoverable thing here.
        self.assertEqual(self._reviewFile(directory).read_text(), "{ this is not a review")

        widget.reset()
        self.delayDisplay("Passed")

    def test_a_signed_off_case_reopens_locked(self):
        self.delayDisplay("A case signed off in an earlier session comes back locked")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._openCase(widget, directory)

        for row, verdict in enumerate(("Yes", "No", "Not assessable")):
            self._setVerdict(widget, row, verdict)
        widget.onSignOff()

        # Away and back, the way the next session reaches it: off disk, already finished.
        widget.reset()
        self._openCase(widget, directory)
        self.assertEqual(widget.review["review"]["state"], rev.SIGNED_OFF)

        for row in range(widget.tableWidget.rowCount):
            # Locked on the way in, not only after onSignOff — otherwise the controls invite
            # a change and answer it with an error dialog.
            self.assertFalse(widget.tableWidget.cellWidget(row, COL_VERDICT).enabled)
            self.assertFalse(widget.tableWidget.cellWidget(row, COL_LIKERT).enabled)
            self.assertFalse(widget.tableWidget.cellWidget(row, COL_REASON).enabled)
            self.assertFalse(widget.tableWidget.item(row, COL_NOTE).flags() & qt.Qt.ItemIsEditable)

        widget.reset()
        self.delayDisplay("Passed")

    def test_locking_the_table_records_no_notes(self):
        self.delayDisplay("Opening and signing off a case records nothing the reviewer did not do")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._openCase(widget, directory)

        # Just opened: nothing decided, nothing noted, still "received".
        self.assertEqual(widget.review["events"], [])
        self.assertEqual(widget.review["review"]["state"], rev.RECEIVED)

        for row, verdict in enumerate(("Yes", "No", "Not assessable")):
            self._setVerdict(widget, row, verdict)
        widget.onSignOff()

        onDisk = rev.load(self._reviewFile(directory))
        self.assertEqual(onDisk["review"]["state"], rev.SIGNED_OFF)
        self.assertEqual([e["what"] for e in onDisk["events"] if e["what"] == "note"], [])

        widget.reset()
        self.delayDisplay("Passed")

    def test_the_arrow_keys_move_the_view_with_the_highlight(self):
        self.delayDisplay("Moving the current row from the keyboard reformats onto it")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._openCase(widget, directory)

        widget.tableWidget.setCurrentCell(2, COL_CONFIDENCE)
        self.assertEqual(widget.selected_detection, 2)
        d = widget.detections[2]
        np.testing.assert_allclose(widget.candidateOrigin, (np.array(d["P1"]) + np.array(d["P2"])) / 2)

        # Back onto the same row after "Unselect all" takes a click, not a cell change.
        widget.unselectAllRows()
        widget.onRowClicked(widget.tableWidget.model().index(2, COL_CONFIDENCE))
        self.assertEqual(widget.selected_detection, 2)

        widget.reset()
        self.delayDisplay("Passed")

    def test_a_refused_verdict_is_not_left_on_screen(self):
        self.delayDisplay("A verdict the review refuses is taken back off the table")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._openCase(widget, directory)

        # A candidate the review has no entry for refuses every verdict.
        del widget.review["detections"][widget.detections[0][det.SOURCE_KEY]]
        self._setVerdict(widget, 0, "Yes")
        self.assertEqual(widget.tableWidget.cellWidget(0, COL_VERDICT).currentIndex, 0)
        widget.tableWidget.cellWidget(0, COL_LIKERT).setCurrentIndex(3)
        self.assertEqual(widget.tableWidget.cellWidget(0, COL_LIKERT).currentIndex, 0)

        widget.reset()
        self.delayDisplay("Passed")

    def _signOffAll(self, widget):
        for row in range(widget.tableWidget.rowCount):
            self._setVerdict(widget, row, "Yes")
        widget.onSignOff()
        self.assertEqual(widget.review["review"]["state"], rev.SIGNED_OFF)

    def test_a_signed_off_case_takes_no_new_finding(self):
        self.delayDisplay("A finding cannot be added to a signed-off case, and placed points survive")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._openCase(widget, directory)

        # Points being placed when the case is signed off.
        widget.addAnnotationNode()
        widget.tmpMark.AddControlPoint([10.0, 0.0, 0.0])
        widget.tmpMark.AddControlPoint([10.0, 0.0, 6.0])
        self._signOffAll(widget)

        widget.validateAnnotation()  # refused, without losing the points
        self.assertEqual(len(widget.detections), 3)
        self.assertIsNotNone(widget.tmpMark)
        self.assertEqual(widget.tmpMark.GetNumberOfControlPoints(), 2)

        widget.removeAnnotationNodes()
        self.assertFalse(widget.ui.addButton.enabled)
        # Clean leaves no case to lock, so Add is offered again.
        widget.reset()
        self.assertTrue(widget.ui.addButton.enabled)
        self.delayDisplay("Passed")

    def test_reopening_takes_in_candidates_added_since_sign_off(self):
        self.delayDisplay("A candidate the detector added after sign-off can be decided after a reopen")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._openCase(widget, directory)
        self._signOffAll(widget)
        widget.reset()

        detectionFile = directory / "detections.json"
        detections = json.loads(detectionFile.read_text())
        detections["late_one"] = {"P1": [0.0, 0.0, 0.0], "P2": [0.0, 0.0, 4.0], "confidence": 0.01}
        detectionFile.write_text(json.dumps(detections))
        self._openCase(widget, directory)
        row = next(i for i, d in enumerate(widget.detections) if d[det.SOURCE_KEY] == "late_one")

        widget.reopenCase("the detector was rerun")
        self.assertEqual(rev.entry(widget.review, "late_one")["state"], rev.UNREVIEWED)
        self._setVerdict(widget, row, "No")
        self.assertEqual(rev.entry(rev.load(self._reviewFile(directory)), "late_one")["state"], rev.REJECTED)

        widget.reset()
        self.delayDisplay("Passed")

    def _previewCount(self):
        return len(slicer.util.getNodes("Tmp Sphere*", useLists=True))

    def test_placing_leaves_place_mode_and_one_preview(self):
        self.delayDisplay("The second point ends placement, and a stray point adds no preview")
        widget = self._widget()
        widget.reset()
        widget.volNode = self._addTestVolume()
        interaction = slicer.app.applicationLogic().GetInteractionNode()

        widget.addAnnotationNode()
        self.assertEqual(interaction.GetCurrentInteractionMode(), interaction.Place)
        widget.tmpMark.AddControlPoint([10.0, 0.0, 0.0])
        widget.tmpMark.AddControlPoint([10.0, 0.0, 6.0])
        self.assertNotEqual(interaction.GetCurrentInteractionMode(), interaction.Place)
        self.assertEqual(self._previewCount(), 1)

        widget.tmpMark.AddControlPoint([10.0, 0.0, 9.0])
        self.assertEqual(self._previewCount(), 1)
        widget.removeAnnotationNodes()  # Cancel
        self.assertEqual(self._previewCount(), 0)

        widget.reset()
        self.delayDisplay("Passed")

    def test_closing_the_scene_mid_placement_ends_it(self):
        self.delayDisplay("A placement under way does not survive the scene it was in")
        widget = self._widget()
        widget.reset()
        widget.addAnnotationNode()
        widget.tmpMark.AddControlPoint([10.0, 0.0, 0.0])

        slicer.mrmlScene.Clear(0)
        self.assertIsNone(widget.tmpMark)
        self.assertFalse(widget.ui.okButton.enabled)
        self.assertTrue(widget.ui.addButton.enabled)
        self.delayDisplay("Passed")

    def _roiCount(self):
        return slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLMarkupsROINode")

    def test_opening_and_closing_cases_leaves_no_roi_behind(self):
        self.delayDisplay("Open and Clean, with and without crop, add no ROI to the scene")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        before = self._roiCount()

        widget.openCase(directory)
        widget.reset()
        widget.ui.cropBox.checked = True
        widget.openCase(directory)
        widget.reset()
        widget.ui.cropBox.checked = False

        self.assertEqual(self._roiCount(), before)
        self.delayDisplay("Passed")

    def test_closing_without_volume_rendering_still_saves_the_angle(self):
        self.delayDisplay("Closing after Volume Rendering is unloaded, as at exit, still saves")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        # The whole path, volume and its rendering nodes included: those are what reset()
        # reaches Volume Rendering for.
        widget.openCase(directory)
        self.assertIsNotNone(widget.volumeRenderingDisplayNode())
        key = widget.detections[1][det.SOURCE_KEY]
        widget.onRowSelected(1)
        widget.rotateAxisSlider(42)

        # Slicer unloads Volume Rendering before this module's widget when it exits.
        volumeRendering = slicer.modules.volumerendering
        del slicer.modules.volumerendering
        try:
            widget.reset()
        finally:
            slicer.modules.volumerendering = volumeRendering

        self.assertEqual(rev.entry(rev.load(self._reviewFile(directory)), key)["viewAngle"], 42)
        self.delayDisplay("Passed")

    def test_a_new_case_takes_the_colour_and_crop_on_screen(self):
        self.delayDisplay("The colour and crop already set apply to the next case opened")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        originalColour = widget.ui.volumeColor.color
        widget.ui.volumeColor.color = qt.QColor(255, 0, 0)
        widget.ui.cropBox.checked = True

        widget.openCase(directory)
        displayNode = widget.volumeRenderingDisplayNode()
        self.assertTrue(displayNode.GetCroppingEnabled())
        colour = displayNode.GetVolumePropertyNode().GetColor()
        self.assertEqual(tuple(colour.GetColor(colour.GetRange()[0])), (1.0, 0.0, 0.0))

        widget.ui.cropBox.checked = False
        widget.reset()
        widget.ui.volumeColor.color = originalColour
        self.delayDisplay("Passed")

    def test_moving_between_rows_rewrites_the_review_only_for_a_new_angle(self):
        self.delayDisplay("Browsing rows leaves the review file alone; a new angle is written")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._openCase(widget, directory)
        reviewFile = self._reviewFile(directory)

        # A first visit records the angle the row opens at: candidate_1's comes from the
        # detection file, which the new review does not hold yet.
        for row in (0, 1, 2):
            widget.onRowSelected(row)
        widget.onRowSelected(-1)
        before = reviewFile.stat().st_mtime_ns
        for row in (0, 1, 2, 0):
            widget.onRowSelected(row)
        self.assertEqual(reviewFile.stat().st_mtime_ns, before)

        widget.rotateAxisSlider(42)
        widget.onRowSelected(1)
        self.assertEqual(rev.entry(rev.load(reviewFile), widget.detections[0][det.SOURCE_KEY])["viewAngle"], 42)

        widget.reset()
        self.delayDisplay("Passed")

    def test_the_last_view_angle_survives_clean(self):
        self.delayDisplay("The angle the reviewer left the last row at is written out")
        widget = self._widget()
        widget.reset()
        directory = self._buildCase()
        self._openCase(widget, directory)

        key = widget.detections[1][det.SOURCE_KEY]
        widget.onRowSelected(1)
        widget.rotateAxisSlider(42)
        # Nothing else here asks for a save — no verdict, no annotation — so the angle is
        # still only in memory, which is exactly the case that used to lose it.
        widget.reset()

        onDisk = rev.load(self._reviewFile(directory))
        self.assertEqual(rev.entry(onDisk, key)["viewAngle"], 42)
        self.delayDisplay("Passed")

    def test_a_note_on_a_candidate_with_no_case_open_is_ignored(self):
        self.delayDisplay("A note typed against a bare volume is dropped, not a traceback")
        widget = self._widget()
        widget.reset()
        widget.volNode = self._addTestVolume()

        # No detection file, so no review and no key: exactly what load_files leaves behind
        # for a directory holding a volume alone.
        widget.prepareAnnotationNode()
        widget.tmpMark.AddControlPoint([10.0, 0.0, 0.0])
        widget.tmpMark.AddControlPoint([10.0, 0.0, 6.0])
        widget.validateAnnotation()
        self.assertIsNone(widget.review)
        self.assertNotIn(det.SOURCE_KEY, widget.detections[0])

        # Used to raise KeyError from the partial's arguments, before applyToReview could
        # decline the change. Called directly as well as through the signal, because PythonQt
        # swallows an exception raised inside a slot — it prints the traceback and lets
        # setText return normally, so the signal path alone would pass either way.
        item = widget.tableWidget.item(0, COL_NOTE)
        item.setText("looks like an infundibulum")
        widget.onNoteEdited(item)

        widget.reset()
        self.delayDisplay("Passed")

    def test_the_volume_colour_picker_survives_missing_rendering_nodes(self):
        self.delayDisplay("Recolouring a volume with no rendering display node does nothing")
        widget = self._widget()
        widget.reset()
        # Added straight to the scene, so it has no volume-rendering display node — the state
        # Clean leaves behind, and the one every other call site here already guards for.
        widget.volNode = self._addTestVolume()

        widget.set_volume_color()

        widget.reset()
        self.delayDisplay("Passed")

    def test_the_data_probe_is_hidden_while_the_module_is_open(self):
        self.delayDisplay("The data probe is hidden on entry and put back on the way out")
        widget = self._widget()
        probe = widget.dataProbeWidget()
        if probe is None:
            self.delayDisplay("Skipped: this session has no data probe")
            return

        probe.setVisible(True)
        widget.enter()
        self.assertFalse(probe.visible)
        widget.exit()
        self.assertTrue(probe.visible)

        # A reviewer who had already collapsed it does not find it reopened.
        probe.setVisible(False)
        widget.enter()
        widget.exit()
        self.assertFalse(probe.visible)

        widget.enter()
        self.delayDisplay("Passed")

    def test_a_case_opens_without_disturbing_the_rest_of_the_scene(self):
        self.delayDisplay("Another module's volume and ROI survive a case being opened")
        widget = self._widget()
        widget.reset()

        # Somebody else's data, of exactly the two classes this module used to adopt by
        # taking the first node of a class it found.
        foreignVolume = self._addTestVolume()
        foreignVolume.SetName("ForeignVolume")
        foreignRoi = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsROINode", "ForeignROI")

        directory = self._buildCase()
        widget.openCase(directory)

        # The case's own volume, not whichever one was already in the scene.
        self.assertIsNotNone(widget.volNode)
        self.assertNotEqual(widget.volNode.GetID(), foreignVolume.GetID())
        self.assertEqual(len(widget.detections), 3)

        # Cropping makes the module its own ROI rather than seizing the one already there.
        widget.ui.cropBox.checked = True
        widget.onCropChanged()
        self.assertIsNotNone(widget.currentRoiNode())
        self.assertNotEqual(widget.roiNode.GetID(), foreignRoi.GetID())

        widget.reset()
        self.assertTrue(slicer.mrmlScene.IsNodePresent(foreignVolume))
        self.assertTrue(slicer.mrmlScene.IsNodePresent(foreignRoi))
        # ...and the module's own ROI did go.
        self.assertIsNone(widget.currentRoiNode())

        widget.ui.cropBox.checked = False
        slicer.mrmlScene.RemoveNode(foreignVolume)
        slicer.mrmlScene.RemoveNode(foreignRoi)
        self.delayDisplay("Passed")

    def test_module_loads(self):
        self.delayDisplay("Module loads and builds its widget")
        widget = self._widget()
        self.assertIsNotNone(widget)
        self.assertEqual(widget.selected_detection, -1)
        self.delayDisplay("Passed")

    def test_detections_become_scene_nodes(self):
        self.delayDisplay("A detection file becomes spheres, lines and markups")
        widget = self._widget()
        widget.reset()

        sample = self._samplePath()
        if not sample.exists():
            self.delayDisplay(f"Skipped: no sample data at {sample}")
            return

        widget.load_detections(sample)
        widget.create_spheres()
        self.assertEqual(len(widget.detections), 3)
        # The index-parallel invariant: one sphere and one line per detection, and one
        # control point per detection in each of the three markups nodes.
        self.assertEqual(len(widget.spheres), len(widget.detections))
        self.assertEqual(len(widget.lines), len(widget.detections))
        for markups in (widget.bases, widget.tips, widget.confidences):
            self.assertEqual(markups.GetNumberOfControlPoints(), len(widget.detections))

        widget.reset()
        self.assertEqual(widget.spheres, [])
        self.assertEqual(widget.bases.GetNumberOfControlPoints(), 0)
        self.delayDisplay("Passed")

    def test_selecting_a_row_aligns_the_red_slice(self):
        self.delayDisplay("Selecting a detection puts its axis in the Red slice")
        widget = self._widget()
        widget.reset()

        sample = self._samplePath()
        if not sample.exists():
            self.delayDisplay(f"Skipped: no sample data at {sample}")
            return

        widget.volNode = self._addTestVolume()
        widget.load_detections(sample)
        widget.create_spheres()

        sliceNode = slicer.mrmlScene.GetNodeByID("vtkMRMLSliceNodeRed")
        for row in range(len(widget.detections)):
            widget.onRowSelected(row)
            m = slicer.util.arrayFromVTKMatrix(sliceNode.GetSliceToRAS())
            normal, origin, sliceY = m[:3, 2], m[:3, 3], m[:3, 1]

            # The three invariants the reformatting exists for: the slice contains the
            # candidate's axis, it passes through the candidate, and the axis points along slice
            # +Y so it stays upright rather than tumbling. Measure the distance to the plane
            # rather than to the point, because Slicer may slide the origin along the normal
            # when it snaps the slice to the voxel grid.
            self.assertAlmostEqual(float(normal @ widget.candidateAxis), 0.0, places=6)
            self.assertLess(abs(float((widget.candidateOrigin - origin) @ normal)), 1.0)
            self.assertAlmostEqual(float(sliceY @ widget.candidateAxis), 1.0, places=3)

        widget.reset()
        self.delayDisplay("Passed")

    def test_closing_the_scene_resets_the_widget(self):
        self.delayDisplay("Closing the scene empties the widget state")
        widget = self._widget()
        widget.reset()

        sample = self._samplePath()
        if not sample.exists():
            self.delayDisplay(f"Skipped: no sample data at {sample}")
            return

        widget.volNode = self._addTestVolume()
        widget.load_detections(sample)
        widget.create_spheres()
        for d in widget.detections:
            widget.add_detection_to_table(d)
        widget.onRowSelected(0)
        self.assertEqual(widget.tableWidget.rowCount, 3)

        # What File -> Close Scene does.
        slicer.mrmlScene.Clear()

        self.assertEqual(widget.detections, [])
        self.assertEqual(widget.spheres, [])
        self.assertEqual(widget.lines, [])
        self.assertEqual(widget.tableWidget.rowCount, 0)
        self.assertEqual(widget.selected_detection, -1)
        self.assertIsNone(widget.volNode)
        self.assertIsNone(widget.volProp)
        # Usable again straight away: the three session-long markups nodes are back in the
        # scene, so the next patient's control points have somewhere to go.
        for markups in (widget.bases, widget.tips, widget.confidences):
            self.assertTrue(slicer.mrmlScene.IsNodePresent(markups))
            self.assertEqual(markups.GetNumberOfControlPoints(), 0)
        self.delayDisplay("Passed")
