import functools
import os

import numpy as np
import qt
import slicer
import vtk
import vtkmodules

# The Slicer-free core: geometry, detection file format and display maths. See
# ReformatVizLib/__init__.py for why the boundary exists.
from ReformatVizLib import detections as det
from ReformatVizLib import geometry, rendering
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
TABLE_COLUMNS = ['Confidence (%)', 'Diameter (mm)', 'Aneurysm ?', 'Likert']

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
        self.parent.title = "Reformat Aneurysm"  # TODO: make this more human readable by adding spaces
        self.parent.categories = ["Aneurysms"]
        self.parent.dependencies = []
        self.parent.contributors = ["Youssef Assis and Erwan Kerrien (Loria lab.)"]
        self.parent.acknowledgementText = "This module was developed by Youssef Assis, and is maintained by Erwan Kerrien"

class ReformatVizWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    def __init__(self, parent=None):
        """
        Called when the user opens the module the first time and the widget is initialized.
        """
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)  # needed to observe the scene being closed

        slicer.app.pythonConsole().clear()
        self.clearScene()

    def setup(self):
        """
        Called when the user opens the module the first time and the widget is initialized.
        """
        ScriptedLoadableModuleWidget.setup(self)
        slicer.util.findChild(slicer.util.mainWindow(), "DataProbeCollapsibleWidget").setVisible(False)

        # Load widget from .ui file (created by Qt Designer).
        # Additional widgets can be instantiated manually and added to self.layout.
        uiWidget = slicer.util.loadUI(self.resourcePath('UI/ReformatViz.ui'))
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

    def connectVolumeControls(self):
        self.ui.load_volume_button.clicked.connect(self.load_files) # load volume
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

        self.ui.addButton.connect('clicked(bool)', self.addAnnotationNode)
        self.ui.cancelButton.connect('clicked(bool)', self.removeAnnotationNodes)
        self.ui.okButton.connect('clicked(bool)', self.validateAnnotation)

    def connectReviewControls(self):
        self.ui.saveButton.clicked.connect(self.save)
        # The lambda swallows clicked's bool argument, which would otherwise bind to
        # reset_view: the button always emits False, so connecting the slot directly meant
        # unselecting never reset the view.
        self.ui.unselectButton.connect('clicked(bool)', lambda *_: self.unselectAllRows())
        self.ui.cleanButton.connect('clicked(bool)', self.reset)

    def connectViewControls(self):
        self.ui.axialVisibility.stateChanged.connect(self.set_axial_slice_visibility)
        self.ui.rotateAxisSlider.valueChanged.connect(self.rotateAxisSlider)
        self.ui.centerCamera.clicked.connect(self.centerCamera)
        self.ui.thicknessSlider.valueChanged.connect(self.thicknessSlider)

    def initState(self):
        """Create the session-long nodes and the state the rest of the widget assumes."""
        self.ensureMarkupsNodes()
        self.spheres = []
        self.lines = []

        self.selected_detection = -1
        self.detections = []

        self.tmpMark : slicer.vtkMRMLMarkupsFiducialNode = None
        self.tmpSphereSource : slicer.vtkSphereSource = None
        self.tmpSphere : slicer.vtkMRMLModelNode = None

        self.volNode : slicer.vtkMRMLScalarVolumeNode = None
        self.volProp : vtkmodules.vtkRenderingCore.vtkVolumeProperty=None
        self.scalarOpacity=makeScalarOpacity()

        self.prepare_slices()

    def buildReviewTable(self):
        """Build the review table. Its signals are connected here, once — see the note in
        add_detection_to_table, which runs per row."""
        self.tableWidget = qt.QTableWidget()
        self.tableWidget.setColumnCount(len(TABLE_COLUMNS))
        self.tableWidget.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.tableWidget.horizontalHeader().setStretchLastSection(True)
        self.tableWidget.resizeColumnsToContents()
        self.tableWidget.setSelectionBehavior(self.tableWidget.SelectRows)
        self.tableWidget.setSelectionMode(self.tableWidget.SingleSelection)
        self.tableWidget.clicked.connect(self.onRowSelected)

        self.layout_2 = qt.QGridLayout()
        self.layout_2.addWidget(self.tableWidget)
        self.ui.CollapsibleButton.setLayout(self.layout_2)

    def set_volume_color(self, color=None):
        color = rendering.to_rgb(color if color is not None else self.ui.volumeColor.color)
        if self.volNode:
            displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(self.volNode)
            rangeCT = [0] * 2
            displayNode.GetVolumePropertyNode().GetColor().GetRange(rangeCT)
            colorTransferFunction = vtk.vtkColorTransferFunction()
            colorTransferFunction.AddRGBPoint(rangeCT[0], color[0], color[1], color[2])
            colorTransferFunction.AddRGBPoint(rangeCT[1], color[0], color[1], color[2])
            displayNode.GetVolumePropertyNode().SetColor(colorTransferFunction)

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

    def onVolumeOpacityChanged(self,value):
        self.updateScalarOpacity()

    def onCropVisibilityChanged(self):
        roiNode = slicer.util.getNodesByClass("vtkMRMLMarkupsROINode")
        if len(roiNode) > 0:
            roiNode=roiNode[0]
            roiNode.GetDisplayNode().SetVisibility(self.ui.cropVisBox.checked)

    def onCropChanged(self):
        roiNode = slicer.util.getNodesByClass("vtkMRMLMarkupsROINode")
        if (not self.ui.cropBox.checked) or (not self.volNode):
            if len(roiNode)>0:
                self.ui.cropVisBox.setChecked(False)
                # This branch is also the "no volume loaded" one, and the lookup needs a
                # volume node to search from.
                if self.volNode:
                    displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(self.volNode)
                    if displayNode:
                        displayNode.SetCroppingEnabled(0)
        else:
            self.ui.cropVisBox.setChecked(True)
            # print("turn on ROI")
            if len(roiNode) == 0:
                # First time? then Create
                roiNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsROINode")

                roiNode.GetDisplayNode().SetOpacity(0.05)
                roiNode.GetDisplayNode().SetVisibility(True)
            else:
                roiNode = roiNode[0]
            roiNode.SetName('') # undisplay ROI name
            if self.selected_detection >= 0:
                position_RAS = self.detections[self.selected_detection]['center']
                radius = [self.detections[self.selected_detection]['radius']*4 ] * 3
                roiNode.SetCenter(position_RAS)
                roiNode.SetRadiusXYZ(radius)
            else:
                m=vtkmodules.vtkCommonMath.vtkMatrix4x4()
                self.volNode.GetIJKToRASMatrix(m)
                center,size=geometry.volume_bounds(slicer.util.arrayFromVTKMatrix(m),
                                                   self.volNode.GetImageData().GetDimensions())
                roiNode.SetCenter(center.tolist())
                roiNode.SetSize(size.tolist())
            displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(self.volNode)
            if displayNode:
                displayNode.SetCroppingEnabled(True)
                displayNode.SetAndObserveROINodeID(roiNode.GetID())
                displayNode.GetROINode().SetDisplayVisibility(True)


    def rotateAxisSlider(self, value):
        node = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed')
        if node is not None:
            if self.detections and self.selected_detection >= 0:
                self.detections[self.selected_detection]['viewAngle']=value
            self.reformatLogic.SetSliceNormal(node, geometry.slice_normal(self.aneRz, value))
            # Read back the slice axes Slicer settled on, then roll in-plane so the
            # aneurysm stays upright instead of tumbling as the reviewer rotates.
            m=node.GetSliceToRAS()
            sliceX=np.array(m.MultiplyDoublePoint([1,0,0,0]))[:3]
            sliceY=np.array(m.MultiplyDoublePoint([0,1,0,0]))[:3]
            self.reformatLogic.RotateSlice(node,2,geometry.in_plane_roll_deg(sliceX,sliceY,self.aneAxis))
            self.reformatLogic.SetSliceOrigin(node, self.aneOrigin)

        node = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeGreen')
        if node is not None:
            self.reformatLogic.SetSliceOrigin(node, self.aneOrigin)
        node = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeYellow')
        if node is not None:
            self.reformatLogic.SetSliceOrigin(node, self.aneOrigin)


    def set_axial_slice_visibility(self):
        layoutManager = slicer.app.layoutManager()
        controller = layoutManager.sliceWidget("Red").sliceController()
        controller.setSliceVisible(self.ui.axialVisibility.checked)

    def thicknessSlider(self,value):
        node = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed')
        if node is None:
            return
        node.SetSlabReconstructionEnabled(1)
        node.SetSlabReconstructionType(1) # Max Intensity Projection
        node.SetSlabReconstructionThickness(value)


    def load_files(self):
        # save before loading
        dir_path = qt.QFileDialog().getExistingDirectory()
        if dir_path:
            # load the first volume file
            nii_volume_files = sorted([f for f in os.listdir(dir_path) if f.endswith(".nii.gz")])
            detection_files = sorted([f for f in os.listdir(dir_path) if f.endswith(".json")])
            if nii_volume_files:
                self.reset()

                slicer.util.loadVolume(os.path.join(dir_path, nii_volume_files[0]))

                self.volNode = slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")[0]
                # volume renderings view
                volRenLogic = slicer.modules.volumerendering.logic()
                displayNode = volRenLogic.GetFirstVolumeRenderingDisplayNode(self.volNode)
                if not displayNode:
                    displayNode = volRenLogic.CreateDefaultVolumeRenderingNodes(self.volNode)
                volRenLogic.UpdateDisplayNodeFromVolumeNode(displayNode, self.volNode)

                self.volProp=displayNode.GetVolumePropertyNode().GetVolumeProperty()

                # define the scalarOpacity function. Each of these three assignments emits
                # valueChanged, so unblocked they rebuild the ramp against a range that is
                # only half updated; the explicit call afterwards is the one that counts.
                min_val, max_val = self.volNode.GetImageData().GetScalarRange()
                wasBlocked = self.ui.volumeThreshold.blockSignals(True)
                self.ui.volumeThreshold.maximum = max_val
                self.ui.volumeThreshold.minimum = min_val
                self.ui.volumeThreshold.value = (max_val+min_val)/3
                self.ui.volumeThreshold.blockSignals(wasBlocked)
                self.updateScalarOpacity()

                self.ui.vol_visibility.checked = 1

                # detections
                if detection_files:
                    self.load_detections(os.path.join(dir_path, detection_files[0]))
                    self.create_spheres()
                    for d in self.detections:
                        self.add_detection_to_table(d)

    def load_detections(self, file_path):
        # Parsing and validation live in ReformatVizLib; this method only turns a failure
        # into something the reviewer sees. DetectionFormatError and json's decode error are
        # both ValueError.
        try:
            self.detections = det.load(file_path)
        except (OSError, ValueError) as e:
            slicer.util.errorDisplay(det.describe_load_failure(file_path, e))
            self.detections = []

    # index is necessary because the table is not updated immediately after an insertion, so one cannot rely on rowCount
    def add_detection_to_table(self,d):
        idx=self.tableWidget.rowCount
        self.tableWidget.insertRow(idx)
        # needed here because columns headers require at least one row...
        self.tableWidget.setColumnCount(len(TABLE_COLUMNS))
        self.tableWidget.setHorizontalHeaderLabels(TABLE_COLUMNS)

        # Confidence score
        itm = qt.QTableWidgetItem(str(det.confidence_percent(d)))
        itm.setTextAlignment(4)
        self.tableWidget.setItem(idx, 0,  itm)

        # Diameter (mm)
        itm = qt.QTableWidgetItem(str(det.diameter(d)))
        itm.setTextAlignment(4)
        self.tableWidget.setItem(idx, 1,  itm)

        cbox = qt.QCheckBox()
        cbox.checked = int(d['aneurysm'])
        cell_widget = qt.QWidget()
        lay_out = qt.QHBoxLayout(cell_widget)
        lay_out.addWidget(cbox)
        lay_out.setAlignment(qt.Qt.AlignCenter)
        lay_out.setContentsMargins(0,0,0,0)
        cell_widget.setLayout(lay_out)
        self.tableWidget.setCellWidget(idx, 2, cell_widget)


        # Likert score
        lv = qt.QComboBox()
        for i in range(6):
            lv.addItem('-' if i == 0 else i)
        lv.setCurrentIndex(d.get('Likert', det.LIKERT_UNSCORED))
        self.tableWidget.setCellWidget(idx, 3, lv)
        lv.currentIndexChanged.connect(lambda index: det.set_likert_score(d, index))

        self.tableWidget.setCurrentItem(self.tableWidget.item(self.selected_detection,0))
        cbox.stateChanged.connect(functools.partial(self.confirmAneurysm,det=d))

    def confirmAneurysm(self, state, detection):
        detection['aneurysm']=bool(state)

    def onRowSelected(self, item):
        self.selected_detection  = item.row() if not isinstance(item, int) else item

        self.updateObjectsDisplay()
        self.prepare_slices()

    def unselectAllRows(self,reset_view=True):
        self.tableWidget.clearSelection()
        self.selected_detection=-1
        self.updateObjectsDisplay()
        if reset_view:
            self.prepare_slices()

    def create_spheres(self):

        for d in self.detections:
            self.add_detection_to_scene(d)

        self.updateObjectsDisplay()

    def updateObjectsDisplay(self):
        self.set_lines_color()
        self.set_lines_visibility()

        self.set_spheres_color()
        self.set_spheres_opacity()
        self.set_spheres_visibility()


        self.set_markups_color()
        self.set_markups_visibility()

        self.set_confidence_color()
        self.set_confidence_visibility()

    def ensureMarkupsNodes(self):
        """Create the three shared markups nodes, or replace them if they left the scene.

        Closing the scene from the File menu removes them while this widget keeps its
        references, and control points added to a node that is no longer in the scene go
        nowhere: the ostia, domes and confidence labels silently stop appearing.
        """
        for attribute, name in (('ostia', 'Ostium'), ('domes', 'Dome'),
                                ('confidences', 'Confidences')):
            node = getattr(self, attribute, None)
            if node is None or not slicer.mrmlScene.IsNodePresent(node):
                node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
                node.SetName(name)
                node.SetLocked(True)
                setattr(self, attribute, node)
            if not node.GetDisplayNode():
                node.CreateDefaultDisplayNodes()

    def add_detection_to_scene(self,d):
        # Sphere
        sphere = createSphere(d['center'],d['radius'])

        # Assign a unique name to each sphere
        idx=len(self.spheres)
        m=createModel(sphere, name=f'Sphere_{idx}')
        self.spheres.append(m)

        # Line
        line = vtk.vtkLineSource()
        line.SetPoint1(d['P1'])
        line.SetPoint2(d['P2'])
        line.SetResolution(30)
        line.Update()
        m=createModel(line,name=f'Line_{idx}')
        self.lines.append(m)

        self.ensureMarkupsNodes()

        # Confidence score label, the ostium and the dome: the Nth control point of each
        # belongs to detection N.
        n = self.confidences.AddControlPoint(d['center'])
        self.confidences.SetNthControlPointLabel(n, str(det.confidence_percent(d))+" %")
        self.ostia.AddControlPoint(d['P1'])
        self.domes.AddControlPoint(d['P2'])

    def set_spheres_visibility(self):
        for i in range(len(self.detections)):
            self.spheres[i].GetDisplayNode().SetVisibility(
                rendering.is_visible(i, self.selected_detection, self.ui.SpheresVisibility.checked))

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
                rendering.is_visible(i, self.selected_detection, self.ui.LinesVisibility.checked))

    def set_confidence_color(self, color=None):
        if self.confidences.GetDisplayNode():
            color = rendering.to_rgb(color if color is not None else self.ui.ConfidenceColorPicker.color)
            self.confidences.GetDisplayNode().SetColor(color)
            self.confidences.GetDisplayNode().SetSelectedColor(color)

    def set_confidence_visibility(self):
        if self.confidences:
            for i in range(len(self.detections)):
                self.confidences.SetNthControlPointVisibility(
                    i, rendering.is_visible(i, self.selected_detection, self.ui.ConfidenceVisibility.checked))

    def set_markups_color(self, color=None):
        if self.ostia.GetDisplayNode() and self.domes.GetDisplayNode():
            color = rendering.to_rgb(color if color is not None else self.ui.MarkupsColorPicker.color)
            for markups in (self.ostia, self.domes):
                markups.GetDisplayNode().SetColor(color)
                markups.GetDisplayNode().SetSelectedColor(color)

    def set_markups_visibility(self):
        for i in range(len(self.detections)):
            value = rendering.is_visible(i, self.selected_detection, self.ui.MarkupsVisibility.checked)
            self.ostia.SetNthControlPointVisibility(i,value)
            self.domes.SetNthControlPointVisibility(i,value)

    def prepare_slices (self):
        if len(self.detections)==0 or self.selected_detection < 0:
            self.aneAxis,self.aneOrigin,self.aneRz=geometry.default_frame()
            angleValue=0 # need to update slices
        else:
            d=self.detections[self.selected_detection]
            self.aneAxis,self.aneOrigin,self.aneRz=geometry.aneurysm_frame(d["P1"],d["P2"])
            angleValue = d['viewAngle']

        self.reformatLogic = slicer.modules.reformat.logic()

        # Setting the value emits valueChanged, which reformats; the explicit call below has
        # to stay, because switching between two detections saved at the same angle emits
        # nothing while still needing the new frame applied. Blocking the signal keeps that
        # from reformatting the Red slice twice on every row click.
        wasBlocked = self.ui.rotateAxisSlider.blockSignals(True)
        self.ui.rotateAxisSlider.value=angleValue
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
        camera = threeDWidget.threeDView().displayableManagerByClassName('vtkMRMLCameraDisplayableManager').GetCameraWidget().GetCameraNode()

        sliceNode = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed')
        if self.selected_detection < 0 or sliceNode is None:
            upaxis=np.array([0,0,1])
            normal=np.array([0,-1,0])
            origin=np.array([0,0,0])
        else:
            m=sliceNode.GetSliceToRAS()
            upaxis=np.array(m.MultiplyDoublePoint([0,1,0,0]))[:3]
            normal=np.array(m.MultiplyDoublePoint([0,0,1,0]))[:3]
            origin=np.array(m.MultiplyDoublePoint([0,0,0,1]))[:3]

        # keep the current distance, only move the camera around the target
        distance=np.linalg.norm(np.array(camera.GetFocalPoint())-np.array(camera.GetPosition()))

        camera.SetFocalPoint(origin.tolist())
        camera.SetPosition(geometry.camera_position(origin,normal,distance).tolist())
        camera.SetViewUp(upaxis.tolist())


    def set_volume_visibility(self):
        if self.volNode:
            displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(self.volNode)
            if displayNode:
                displayNode.SetVisibility(self.ui.vol_visibility.checked)

    def set_volume_translucent(self):
        self.updateScalarOpacity()

    def prepareAnnotationNode(self, name='P'):
        if self.tmpMark:
            slicer.mrmlScene.RemoveNode(self.tmpMark)
        if self.tmpSphere:
            slicer.mrmlScene.RemoveNode(self.tmpSphere)
            self.tmpSphere : slicer.vtkMRMLModelNode = None

        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode",name)
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

        self.ui.addButton.setEnabled(False)
        self.ui.okButton.setEnabled(True)
        self.ui.cancelButton.setEnabled(True)

    def removeAnnotationNodes(self,caller=None, event=None):
        if self.tmpMark:
            slicer.mrmlScene.RemoveNode(self.tmpMark)
        if self.tmpSphere:
            slicer.mrmlScene.RemoveNode(self.tmpSphere)

        self.tmpMark : slicer.vtkMRMLMarkupsFiducialNode = None
        self.tmpSphere : slicer.vtkMRMLModelNode = None
        self.tmpSphereSource : slicer.vtkSphereSource = None

        self.ui.addButton.setEnabled(True)
        self.ui.okButton.setEnabled(False)
        self.ui.cancelButton.setEnabled(False)

    def validateAnnotation(self):
        markups = self.tmpMark
        if not markups or markups.GetNumberOfControlPoints() != 2:
            # Otherwise OK looks broken: nothing happens and Add stays greyed out, with no
            # indication that the ostium and the dome both have to be placed first.
            slicer.util.warningDisplay("Place two points — the ostium, then the dome — "
                                       "before pressing OK, or press Cancel to start over.")
            return

        p1, p2 = np.empty(3), np.empty(3)
        markups.GetNthControlPointPositionWorld(0, p1)
        markups.GetNthControlPointPositionWorld(1, p2)
        self.removeAnnotationNodes()

        detection = det.from_points(p1, p2)
        self.detections.append(detection)
        self.add_detection_to_table(detection)
        self.add_detection_to_scene(detection)
        self.selected_detection = self.tableWidget.rowCount-1
        self.tableWidget.setCurrentItem(self.tableWidget.item(self.selected_detection,0))
        self.onRowSelected(self.selected_detection)
        self.updateObjectsDisplay()
        self.ui.addButton.setEnabled(True)
        self.ui.okButton.setEnabled(False)
        self.ui.cancelButton.setEnabled(False)


    def drawAnnotationSpheres(self, caller=None, event=None):
        markups : slicer.vtkMRMLMarkupsFiducialNode = caller
        nb_points = markups.GetNumberOfControlPoints()
        if nb_points < 2:
            return

        slicer.modules.markups.logic().StartPlaceMode(0)
        markups.GetDisplayNode().SetSnapMode(slicer.vtkMRMLMarkupsDisplayNode.SnapModeUnconstrained)
        p1, p2 = np.empty(3), np.empty(3)
        markups.GetNthControlPointPositionWorld(0, p1)
        markups.GetNthControlPointPositionWorld(1, p2)

        detection = det.from_points(p1, p2)
        self.tmpSphereSource = createSphere(detection['center'],detection['radius'])
        self.tmpSphere=createModel(self.tmpSphereSource, name='Tmp Sphere')

        self.tmpSphere.GetDisplayNode().SetVisibility(True)
        color = rendering.to_rgb(self.ui.SpheresColorPicker.color)
        self.tmpSphere.GetDisplayNode().SetColor(color)
        self.tmpSphere.GetDisplayNode().SetOpacity(self.ui.SpheresOpacity.value)


    def MoveSpheres(self, caller=None, event=None):
        markups : slicer.vtkMRMLMarkupsFiducial = caller

        # PointModifiedEvent still fires after a point is deleted, and reading a control
        # point that no longer exists leaves the np.empty buffer holding whatever was in that
        # memory, which then becomes the sphere's centre and radius.
        if self.tmpSphereSource and markups.GetNumberOfControlPoints() >= 2:
            p1, p2 = np.empty(3), np.empty(3)
            markups.GetNthControlPointPositionWorld(0, p1)
            markups.GetNthControlPointPositionWorld(1, p2)
            detection = det.from_points(p1, p2)
            self.tmpSphereSource.SetCenter(detection['center'])
            self.tmpSphereSource.SetRadius(detection['radius'])
            self.tmpSphereSource.Update()
            slicer.util.forceRenderAllViews()




    def save(self):
        dialog = qt.QFileDialog()
        filename = str(dialog.getSaveFileName(None,"Save detections","","JSON files (*.json)"))
        if filename:
            # Same treatment as a failed load: an unwritable path or a stale network mount
            # must reach the reviewer, not just the Python console.
            try:
                det.save(filename, self.detections)
            except OSError as e:
                slicer.util.errorDisplay(det.describe_save_failure(filename, e))

    def reset(self):
        for node in self.spheres:
            slicer.mrmlScene.RemoveNode(node)
        for node in self.lines:
            slicer.mrmlScene.RemoveNode(node)
        self.confidences.RemoveAllControlPoints()
        self.ostia.RemoveAllControlPoints()
        self.domes.RemoveAllControlPoints()
        if self.volNode:
            # The rendering display node and its volume property node outlive the volume, so
            # every load/Clean cycle left a pair of them behind. volProp belongs to the
            # property node: left set, the opacity sliders keep writing into a property whose
            # node has been removed.
            displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(self.volNode)
            if displayNode:
                propertyNode = displayNode.GetVolumePropertyNode()
                slicer.mrmlScene.RemoveNode(displayNode)
                if propertyNode:
                    slicer.mrmlScene.RemoveNode(propertyNode)
            slicer.mrmlScene.RemoveNode(self.volNode)
        roiNode = slicer.util.getNodesByClass("vtkMRMLMarkupsROINode")
        for r in roiNode:
            slicer.mrmlScene.RemoveNode(r)

        self.removeAnnotationNodes()

        self.forgetSceneState()

    def forgetSceneState(self):
        """Bring the widget's own state back in line with an empty scene.

        Removes nothing from the scene: this is the tail of `reset()`, and it is also the
        whole of what a scene closed from the File menu needs, where the nodes have already
        been removed for us.
        """
        self.spheres=[]
        self.lines=[]
        self.detections=[]
        self.volNode : slicer.vtkMRMLScalarVolumeNode = None
        self.volProp = None

        self.tableWidget.setRowCount(0)
        self.tableWidget.setCurrentItem(None)
        self.onRowSelected(-1)

    def clearScene(self):
        """
        Empty the scene. This module assumes it is the only owner of the scene:
        load_files and onCropChanged look nodes up by class and take the first
        match, so data left by another module would be picked up by mistake.
        """
        slicer.mrmlScene.Clear(0)

    def cleanup(self):
        """
        Called when the application closes and the module widget is destroyed.
        """
        # Before clearing, so that onSceneEndClose does not run against a widget that is on
        # its way out.
        self.removeObservers()
        self.clearScene()

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
        self.forgetSceneState()


class ReformatVizTest(ScriptedLoadableModuleTest):
    """The test `slicer_add_python_unittest` registers, and the module's Self Test button.

    This covers only what needs Slicer: that the module loads, that a detection file becomes
    scene nodes, that selecting a row reformats the Red slice, that two placed points become a
    detection, and that closing the scene resets the widget. The geometry and file format are
    covered far more thoroughly by the Slicer-free suite in `tests/`, which is where new cases
    belong unless they genuinely need MRML.
    """

    def setUp(self):
        slicer.mrmlScene.Clear()

    def runTest(self):
        self.setUp()
        self.test_module_loads()
        self.test_detections_become_scene_nodes()
        self.test_selecting_a_row_aligns_the_red_slice()
        self.test_a_hand_placed_annotation_becomes_a_detection()
        self.test_closing_the_scene_resets_the_widget()

    def _widget(self):
        slicer.util.selectModule('ReformatViz')
        return slicer.modules.ReformatVizWidget

    def _samplePath(self):
        # Ships with the source tree, not with the installed extension.
        path = os.path.join(os.path.dirname(__file__), '..', 'tests', 'data', 'detections_sample.json')
        return os.path.normpath(path)

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
        volume = slicer.util.addVolumeFromArray(array, ijkToRAS=ijkToRAS,
                                                name='ReformatVizTestVolume')
        slicer.util.setSliceViewerLayers(background=volume, fit=True)
        return volume

    def test_a_hand_placed_annotation_becomes_a_detection(self):
        self.delayDisplay('Two placed points become a detection the reviewer can score')
        widget = self._widget()
        widget.reset()
        widget.volNode = self._addTestVolume()

        ostium, dome = [10.0, 0.0, 0.0], [10.0, 0.0, 6.0]
        widget.prepareAnnotationNode()
        widget.tmpMark.AddControlPoint(ostium)
        widget.tmpMark.AddControlPoint(dome)
        widget.validateAnnotation()

        self.assertEqual(len(widget.detections), 1)
        detection = widget.detections[0]
        # Placed by the reviewer, not by a detector: 'added' is what keeps it at the end of
        # the table after a save and reload, whatever its confidence.
        self.assertEqual(detection['added'], 1)
        self.assertEqual(detection['center'], [10.0, 0.0, 3.0])
        self.assertAlmostEqual(detection['radius'], 3.0)
        # The row, the sphere, the line and the three markups points all have to arrive
        # together, or the positional invariant between them is already broken.
        self.assertEqual(widget.tableWidget.rowCount, 1)
        self.assertEqual(len(widget.spheres), 1)
        self.assertEqual(len(widget.lines), 1)
        for markups in (widget.ostia, widget.domes, widget.confidences):
            self.assertEqual(markups.GetNumberOfControlPoints(), 1)
        # The temporary placement node and its preview sphere are gone.
        self.assertIsNone(widget.tmpMark)
        self.assertIsNone(widget.tmpSphere)

        widget.reset()
        self.delayDisplay('Passed')

    def test_module_loads(self):
        self.delayDisplay('Module loads and builds its widget')
        widget = self._widget()
        self.assertIsNotNone(widget)
        self.assertEqual(widget.selected_detection, -1)
        self.delayDisplay('Passed')

    def test_detections_become_scene_nodes(self):
        self.delayDisplay('A detection file becomes spheres, lines and markups')
        widget = self._widget()
        widget.reset()

        sample = self._samplePath()
        if not os.path.exists(sample):
            self.delayDisplay(f'Skipped: no sample data at {sample}')
            return

        widget.load_detections(sample)
        widget.create_spheres()
        self.assertEqual(len(widget.detections), 3)
        # The index-parallel invariant: one sphere and one line per detection, and one
        # control point per detection in each of the three markups nodes.
        self.assertEqual(len(widget.spheres), len(widget.detections))
        self.assertEqual(len(widget.lines), len(widget.detections))
        for markups in (widget.ostia, widget.domes, widget.confidences):
            self.assertEqual(markups.GetNumberOfControlPoints(), len(widget.detections))

        widget.reset()
        self.assertEqual(widget.spheres, [])
        self.assertEqual(widget.ostia.GetNumberOfControlPoints(), 0)
        self.delayDisplay('Passed')

    def test_selecting_a_row_aligns_the_red_slice(self):
        self.delayDisplay('Selecting a detection puts its axis in the Red slice')
        widget = self._widget()
        widget.reset()

        sample = self._samplePath()
        if not os.path.exists(sample):
            self.delayDisplay(f'Skipped: no sample data at {sample}')
            return

        widget.volNode = self._addTestVolume()
        widget.load_detections(sample)
        widget.create_spheres()

        sliceNode = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed')
        for row in range(len(widget.detections)):
            widget.onRowSelected(row)
            m = slicer.util.arrayFromVTKMatrix(sliceNode.GetSliceToRAS())
            normal, origin, sliceY = m[:3, 2], m[:3, 3], m[:3, 1]

            # The three invariants the reformatting exists for: the slice contains the
            # aneurysm axis, it passes through the aneurysm, and the axis points along slice
            # +Y so it stays upright rather than tumbling. Measure the distance to the plane
            # rather than to the point, because Slicer may slide the origin along the normal
            # when it snaps the slice to the voxel grid.
            self.assertAlmostEqual(float(normal @ widget.aneAxis), 0.0, places=6)
            self.assertLess(abs(float((widget.aneOrigin - origin) @ normal)), 1.0)
            self.assertAlmostEqual(float(sliceY @ widget.aneAxis), 1.0, places=3)

        widget.reset()
        self.delayDisplay('Passed')

    def test_closing_the_scene_resets_the_widget(self):
        self.delayDisplay('Closing the scene empties the widget state')
        widget = self._widget()
        widget.reset()

        sample = self._samplePath()
        if not os.path.exists(sample):
            self.delayDisplay(f'Skipped: no sample data at {sample}')
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
        for markups in (widget.ostia, widget.domes, widget.confidences):
            self.assertTrue(slicer.mrmlScene.IsNodePresent(markups))
            self.assertEqual(markups.GetNumberOfControlPoints(), 0)
        self.delayDisplay('Passed')
