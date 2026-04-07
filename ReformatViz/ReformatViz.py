import logging
import os, json
from copy import deepcopy

import numpy as np
import math
import functools

import vtk, qt
import vtkmodules
from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction


import slicer
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin


eps=np.finfo(np.float64).resolution

tmp_markups_name='P'

def rotate2Z(v):
    v=np.array(v)
    tn=v@v # tiny bit faster that np.dot(v,v)
    if tn<eps:
        return np.eye(3,dtype=np.float64)

    vn=v/math.sqrt(tn)
    R1=np.array([ -vn[1], vn[0], 0])
    tn=R1@R1

    if tn<eps:
        return np.eye(3, dtype=np.float64)

    R1 = (1.0/math.sqrt(tn)) * np.array(R1, dtype=np.float64)
    tn = math.sqrt(1-vn[2]*vn[2])

    R0 = [ vn[0]*vn[2]/tn, vn[1]*vn[2]/tn, -tn ]

    return np.array([R0,R1,vn],dtype=np.float64)

def makeScalarOpacity():
    scalarOpacity = vtkPiecewiseFunction()
    scalarOpacity.AllowDuplicateScalarsOn()
    scalarOpacity.RemoveAllPoints()
    scalarOpacity.AddPoint(0.0,0.0)
    scalarOpacity.AddPoint(1.0, 0.0)
    scalarOpacity.AddPoint(2.0,1.0)
    scalarOpacity.AddPoint(3.0,1.0)
    scalarOpacity.AddPoint(4.0,1.0)
    return scalarOpacity

def createSphere(center, radius):
    sphere = vtk.vtkSphereSource()
    sphere.SetCenter(center)
    sphere.SetRadius(radius)
    sphere.SetPhiResolution(100)
    sphere.SetThetaResolution(100)
    sphere.Update()
    return sphere
       
def createModel(model, name):
    node = slicer.modules.models.logic().AddModel(model.GetOutput())
    node.SetName(name)
    node.GetDisplayNode().SetVisibility2D(True)
    node.GetDisplayNode().SetSliceIntersectionThickness(2)
    return node

def getSphereCenterAndRadius(markups, startPointIndex):
    # Create two empty numpy arrays of size 3 to hold the positions of the two points.
    A, B = np.zeros(3), np.zeros(3)
    # Get the position of the nth control point (startPointIndex) and store it in A.
    markups.GetNthControlPointPosition(startPointIndex, A)
    # Get the position of the (n+1)th control point and store it in B.
    markups.GetNthControlPointPosition(startPointIndex+1, B)
    # Calculate the midpoint between the two points.
    s = (A + B) / 2
    # Calculate the radius of the sphere using the distance between the two points.
    R = np.linalg.norm(A - B)/2
    # Return the center and radius of the sphere as a tuple.
    return A, B, s, R

def addMissingDetectionInfo(d):
    # assumes at least P1 and P2 are defined
    P1=np.array(d['P1'])
    P2=np.array(d['P2'])

    if not 'confidence' in d:
        d['confidence'] = 1
    if not 'center' in d:
        C=(P1+P2)/2
        d['center'] = C.tolist()
    if not 'radius' in d:
        R=np.linalg.norm(P1-P2)/2
        d['radius'] = R 
    if not 'viewAngle' in d:
        d['viewAngle'] = 0
    if not 'aneurysm' in d:
        d['aneurysm'] = False
    return d

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
        VTKObservationMixin.__init__(self)  # needed for parameter node observation

        slicer.app.pythonConsole().clear()
        self.cleanup()

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

        # Buttons
        #### Volume
        self.ui.load_volume_button.clicked.connect(self.load_files) # load volume
        self.ui.vol_visibility.stateChanged.connect(self.set_volume_visibility)
        self.ui.vol_translucent.stateChanged.connect(self.set_volume_translucent)
        self.ui.cropBox.stateChanged.connect(self.onCropChanged)
        self.ui.cropVisBox.stateChanged.connect(self.onCropVisibilityChanged)

        self.ui.volumeOpacity.valueChanged.connect(self.onVolumeOpacityChanged)
        self.ui.volumeThreshold.valueChanged.connect(self.onVolumeThresholdChanged)

        self.ui.volumeColor.colorChanged.connect(self.set_volume_color)

        #### Annotations
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

        # Save/Unselect/Reset
        self.ui.saveButton.clicked.connect(self.save)
        self.ui.unselectButton.connect('clicked(bool)', self.unselectAllRows)
        self.ui.cleanButton.connect('clicked(bool)', self.reset)

        # slices visibility
        self.ui.axialVisibility.stateChanged.connect(self.set_axial_slice_visibility)    
        
        # Slider: Rotate around axis
        self.ui.rotateAxisSlider.valueChanged.connect(self.rotateAxisSlider)
        self.ui.centerCamera.clicked.connect(self.centerCamera)
        
        # slice thickness
        self.ui.thicknessSlider.valueChanged.connect(self.thicknessSlider)

        self.ostia = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        self.ostia.SetName("Ostium")
        self.ostia.SetLocked(True)
        self.domes = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        self.domes.SetName("Dome")
        self.domes.SetLocked(True)
        self.spheres = []
        self.lines = []
        self.confidences = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        self.confidences.SetName("Confidences")
        self.confidences.SetLocked(True)

        self.patient_path = ''
        self.selected_detection = -1
        self.detections = []
    
        self.tmpMark : slicer.vtkMRMLMarkupsFiducialNode = None
        self.tmpSphereSource : slicer.vtkSphereSource = None
        self.tmpSphere : slicer.vtkMRMLModelNode = None

        self.volNode : slicer.vtkMRMLScalarVolumeNode = None
        self.volProp : vtkmodules.vtkRenderingCore.vtkVolumeProperty=None
        self.scalarOpacity=makeScalarOpacity()
    
        self.margin=20

        self.prepare_slices()
        
        
        self.tableWidget = qt.QTableWidget()
        # self.tableWidget.setMaximumHeight(150)
        columns = ['Confidence (%)', 'Diameter (mm)', 'Aneurysm ?']
        self.tableWidget.setColumnCount(len(columns))
        self.tableWidget.setHorizontalHeaderLabels(columns)
        self.tableWidget.horizontalHeader().setStretchLastSection(True)
        self.tableWidget.resizeColumnsToContents()
        self.tableWidget.setSelectionBehavior(self.tableWidget.SelectRows)
        self.tableWidget.setSelectionMode(self.tableWidget.SingleSelection)

        self.layout_2 = qt.QGridLayout()
        self.layout_2.addWidget(self.tableWidget)
        self.ui.CollapsibleButton.setLayout(self.layout_2)


    def set_volume_color(self, color=None):
        color = [color.red()/255, color.green()/255, color.blue()/255] if color is not None else [self.ui.volumeColor.color.red()/255, self.ui.volumeColor.color.green()/255, self.ui.volumeColor.color.blue()/255] 
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
            min_val = self.ui.volumeThreshold.minimum
            max_val = self.ui.volumeThreshold.maximum
            threshold=self.ui.volumeThreshold.value
            opacity=self.ui.volumeOpacity.value
            high_opacity=0 if self.ui.vol_translucent.checked else opacity
            n=self.scalarOpacity.GetSize()
            p=[[0,0,0.5,0] for _ in range(n)]
            p[0][:2]=[min_val,0]
            p[1][:2]=[threshold - self.margin,0]
            p[2][:2]=[threshold,opacity]
            p[3][:2]=[threshold + self.margin,high_opacity]
            p[4][:2]=[max_val,high_opacity]
            for i in range(n):
                self.scalarOpacity.SetNodeValue(i, p[i])
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
                displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(self.volNode)
                if displayNode: 
                    displayNode.SetCroppingEnabled(0)
                    # print("turn off ROI")
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
                image=self.volNode.GetImageData()
                m=vtkmodules.vtkCommonMath.vtkMatrix4x4()
                self.volNode.GetIJKToRASMatrix(m)
                x,y,z=image.GetDimensions()
                pmin=np.array(m.MultiplyDoublePoint((0,0,0,1)))
                pmax=np.array(m.MultiplyDoublePoint((x,y,z,1)))
                s=np.abs(pmin[:3]-pmax[:3])
                roiNode.SetCenter((0,0,0))
                roiNode.SetSize(s.tolist())
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
            angle=value/180*np.pi
            aneNormal = self.aneRz.T@np.array([math.cos(angle),math.sin(angle),0])
            self.reformatLogic.SetSliceNormal(node, aneNormal)
            m=node.GetSliceToRAS()
            sliceX=np.array(m.MultiplyDoublePoint([1,0,0,0]))[:3]
            sliceY=np.array(m.MultiplyDoublePoint([0,1,0,0]))[:3]
            ca=sliceY@self.aneAxis
            sa=sliceX@self.aneAxis
            a=math.atan2(-sa,ca)/np.pi*180
            self.reformatLogic.RotateSlice(node,2,a)
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
        node.SetSlabReconstructionEnabled(1)
        node.SetSlabReconstructionType(1) # Max Intensity Projection
        node.SetSlabReconstructionThickness(value)


    def load_files(self):
        # save before loading
        dir_path = qt.QFileDialog().getExistingDirectory()
        if dir_path:
            self.patient_path = dir_path
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

                # define the scalarOpacity function
                min_val, max_val = self.volNode.GetImageData().GetScalarRange()
                self.ui.volumeThreshold.maximum = max_val
                self.ui.volumeThreshold.minimum = min_val
                self.ui.volumeThreshold.value = (max_val+min_val)/3
                self.updateScalarOpacity()
                
                self.ui.vol_visibility.checked = 1

                # detections
                if detection_files:
                    self.load_detections(os.path.join(dir_path, detection_files[0]))
                    self.create_spheres()
                    for d in self.detections:
                        self.add_detection_to_table(d)
                    
    def load_detections(self, file_path):
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
            if not data: 
                raise Exception()
            first_item=data[next(iter(data))]
            if not 'P1' in first_item or not 'P2' in first_item:
                raise Exception()
        except:
            slicer.util.errorDisplay('No valid detection file found')
            data={}

        # sort by confidence score
        self.detections=[d for d in data.values()]

        # add missing info
        for d in self.detections:
            addMissingDetectionInfo(d)

        self.detections = sorted(self.detections, key=lambda x:x['confidence'], reverse=True)

    # index is necessary because the table is not updated immediately after an insertion, so one cannot rely on rowCount
    def add_detection_to_table(self,d): 
        idx=self.tableWidget.rowCount
        self.tableWidget.insertRow(idx)
        columns = ['Confidence (%)', 'Diameter (mm)', 'Aneurysm ?']
        # needed here because columns headers require at least one row...
        self.tableWidget.setColumnCount(len(columns))
        self.tableWidget.setHorizontalHeaderLabels(columns)
        # Confidence score
        confidence = round(d['confidence']*100, 3)
        itm = qt.QTableWidgetItem(str(confidence))
        itm.setTextAlignment(4)
        self.tableWidget.setItem(idx, 0,  itm)

        # Diameter (mm)
        size = round(d['radius']*2, 3)
        itm = qt.QTableWidgetItem(str(size))
        itm.setTextAlignment(4)
        self.tableWidget.setItem(idx, 1,  itm)

        self.tableWidget.clicked.connect(self.onRowSelected)

        cbox = qt.QCheckBox()
        cbox.checked = int(d['aneurysm'])
        cell_widget = qt.QWidget()
        lay_out = qt.QHBoxLayout(cell_widget)
        lay_out.addWidget(cbox)
        lay_out.setAlignment(qt.Qt.AlignCenter)
        lay_out.setContentsMargins(0,0,0,0)
        cell_widget.setLayout(lay_out)
        self.tableWidget.setCellWidget(idx, 2, cell_widget)
        self.tableWidget.setCurrentItem(self.tableWidget.item(self.selected_detection,0))
        cbox.stateChanged.connect(functools.partial(self.confirmAneurysm,det=d))

    
    def confirmAneurysm(self, state, det):
        det['aneurysm']=bool(state)

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

        # Confidence scores
        if not self.confidences.GetDisplayNode():
            self.confidences.CreateDefaultDisplayNodes()
        n = self.confidences.AddControlPoint(d['center'])
        self.confidences.SetNthControlPointLabel(n, str(round(d['confidence'] * 100, 3))+" %")
        
        # Confidence scores
        if not self.ostia.GetDisplayNode():
            self.ostia.CreateDefaultDisplayNodes()
        self.ostia.AddControlPoint(d['P1'])

        # Confidence scores
        if not self.domes.GetDisplayNode():
            self.domes.CreateDefaultDisplayNodes() 
        self.domes.AddControlPoint(d['P2'])

    def set_spheres_visibility(self):
        for i in range(len(self.detections)):
            value = self.ui.SpheresVisibility.checked if i == self.selected_detection or self.selected_detection < 0 else False
            self.spheres[i].GetDisplayNode().SetVisibility(value)

    def set_spheres_color(self, color=None):
        color = (color.red()/255, color.green()/255, color.blue()/255) if color is not None else (self.ui.SpheresColorPicker.color.red()/255, self.ui.SpheresColorPicker.color.green()/255, self.ui.SpheresColorPicker.color.blue()/255)
        for i in range(len(self.detections)):
            self.spheres[i].GetDisplayNode().SetColor(color)
    
    def set_spheres_opacity(self):
        for i in range(len(self.detections)):
            self.spheres[i].GetDisplayNode().SetOpacity(self.ui.SpheresOpacity.value)
            
    def set_lines_color(self, color=None):
        color = (color.red()/255, color.green()/255, color.blue()/255) if color is not None else (self.ui.LinesColorPicker.color.red()/255, self.ui.LinesColorPicker.color.green()/255, self.ui.LinesColorPicker.color.blue()/255)
        for i in range(len(self.detections)):
            self.lines[i].GetDisplayNode().SetColor(color)

    def set_lines_visibility(self):
        for i in range(len(self.detections)):
            value = self.ui.LinesVisibility.checked if i == self.selected_detection  or self.selected_detection < 0 else False
            self.lines[i].GetDisplayNode().SetVisibility(value)

    def set_confidence_color(self, color=None):
        if self.confidences.GetDisplayNode():
            r, g, b = self.ui.ConfidenceColorPicker.color.red()/255, self.ui.ConfidenceColorPicker.color.green()/255, self.ui.ConfidenceColorPicker.color.blue()/255
            self.confidences.GetDisplayNode().SetColor(r,g,b)
            self.confidences.GetDisplayNode().SetSelectedColor(r,g,b)

    def set_confidence_visibility(self):
        if self.confidences:
            for i in range(len(self.detections)):
                value = self.ui.ConfidenceVisibility.checked if i == self.selected_detection  or self.selected_detection < 0 else False
                self.confidences.SetNthControlPointVisibility(i,value)

    def set_markups_color(self, color=None):
        if self.ostia.GetDisplayNode() and self.domes.GetDisplayNode():
            r, g, b = self.ui.MarkupsColorPicker.color.red()/255, self.ui.MarkupsColorPicker.color.green()/255, self.ui.MarkupsColorPicker.color.blue()/255
            self.ostia.GetDisplayNode().SetColor(r,g,b)
            self.ostia.GetDisplayNode().SetSelectedColor(r,g,b)
            self.domes.GetDisplayNode().SetColor(r,g,g)
            self.domes.GetDisplayNode().SetSelectedColor(r,g,b)
    
    def set_markups_visibility(self):
        for i in range(len(self.detections)):
            value = self.ui.MarkupsVisibility.checked if i == self.selected_detection  or self.selected_detection < 0 else False
            self.ostia.SetNthControlPointVisibility(i,value)
            self.domes.SetNthControlPointVisibility(i,value)
    
    def prepare_slices (self):
        if len(self.detections)==0 or self.selected_detection < 0: 
            self.aneAxis=np.array([0.0,1.0,0.0])
            #self.aneRz=np.eye(3)
            self.aneOrigin=np.zeros(3)
            angleValue=0 # need to update slices
        else:
            d=self.detections[self.selected_detection]

            A = np.array(d["P1"])
            B = np.array(d["P2"])

            self.aneAxis = B - A
            n=np.linalg.norm(self.aneAxis)
            if n > 1e-8:
                self.aneAxis /= n
            else:
                self.aneAxis=np.array([0.0,1.0,0.0])
            angleValue = d['viewAngle'] 
            self.aneOrigin=(A+B)/2  
            
        self.aneRz=rotate2Z(self.aneAxis)
        self.reformatLogic = slicer.modules.reformat.logic()

        self.ui.rotateAxisSlider.value=angleValue    
        self.rotateAxisSlider(self.ui.rotateAxisSlider.value)
        self.thicknessSlider(self.ui.thicknessSlider.value)
        self.centerCamera()
        self.onCropChanged()

    def centerCamera(self):
        # camera focal point 
        camera = slicer.app.layoutManager().threeDWidget(0).threeDView().displayableManagerByClassName('vtkMRMLCameraDisplayableManager').GetCameraWidget().GetCameraNode()

        if self.selected_detection < 0:
            upaxis=np.array([0,0,1])
            normal=np.array([0,-1,0])
            origin=np.array([0,0,0])
        else:
            m=slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed').GetSliceToRAS()
            upaxis=np.array(m.MultiplyDoublePoint([0,1,0,0]))[:3]
            normal=np.array(m.MultiplyDoublePoint([0,0,1,0]))[:3]
            origin=np.array(m.MultiplyDoublePoint([0,0,0,1]))[:3]
        
        # get camera distance
        p=np.array(camera.GetPosition())
        f=np.array(camera.GetFocalPoint())
        d=np.linalg.norm(f-p)
        new_p=origin-d*normal

        camera.SetFocalPoint(origin.tolist())
        camera.SetPosition(new_p.tolist())
        camera.SetViewUp(upaxis.tolist())

    
    def set_volume_visibility(self):
        if self.volNode:
            displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(self.volNode)
            if displayNode:
                displayNode.SetVisibility(self.ui.vol_visibility.checked)
    
    def set_volume_translucent(self):
        self.updateScalarOpacity()

    def prepareAnnotationNode(self, name=tmp_markups_name):
        # if node is None:
        if self.tmpMark:
            slicer.mrmlScene.RemoveNode(self.tmpMark)
        if self.tmpSphere:
            slicer.mrmlScene.RemoveNode(self.tmpSphere)
            self.tmpSphere : slicer.vtkMRMLModelNode = None

        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        node.SetName(name)
        node.GetDisplayNode().SetSnapMode(slicer.vtkMRMLMarkupsDisplayNode.SnapModeUnconstrained)
        # Added markup event
        node.AddObserver(slicer.vtkMRMLMarkupsNode.PointPositionDefinedEvent, self.drawAnnotationSpheres)
        # Removed markup event
        node.AddObserver((slicer.vtkMRMLMarkupsNode).PointRemovedEvent, self.removeAnnotationNodes)
        # Markup position modified event
        node.AddObserver((slicer.vtkMRMLMarkupsNode).PointModifiedEvent, self.MoveSpheres)

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
        if markups and markups.GetNumberOfControlPoints() == 2:
            p1, p2 = np.empty(3), np.empty(3)
            markups.GetNthControlPointPositionWorld(0, p1)        
            markups.GetNthControlPointPositionWorld(1, p2)
            self.removeAnnotationNodes()

            detection = {"P1": p1.tolist(),
                        "P2": p2.tolist(), 
                        "aneurysm": 1,
                        "added": 1
                        }
            detection = addMissingDetectionInfo(detection)
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


###############################################################""    
    def drawAnnotationSpheres(self, caller=None, event=None):
        markups : slicer.vtkMRMLMarkupsFiducialNode = caller

        nb_points = markups.GetNumberOfControlPoints()
        if nb_points < 2: return
        
        slicer.modules.markups.logic().StartPlaceMode(0)
        
        p1, p2 = np.empty(3), np.empty(3)
        markups.GetNthControlPointPositionWorld(0, p1)        
        markups.GetNthControlPointPositionWorld(1, p2)
        
        detection = {"P1": p1.tolist(),
                    "P2": p2.tolist(), 
                    "aneurysm": 1,
                    "added": 1
                    }
        detection = addMissingDetectionInfo(detection)

        self.tmpSphereSource = createSphere(detection['center'],detection['radius'])
        self.tmpSphere=createModel(self.tmpSphereSource, name=f'Tmp Sphere')

        self.tmpSphere.GetDisplayNode().SetVisibility(True)
        color = (self.ui.SpheresColorPicker.color.red()/255, self.ui.SpheresColorPicker.color.green()/255, self.ui.SpheresColorPicker.color.blue()/255)
        self.tmpSphere.GetDisplayNode().SetColor(color)
        self.tmpSphere.GetDisplayNode().SetOpacity(self.ui.SpheresOpacity.value)

 
    def MoveSpheres(self, caller=None, event=None):
        markups : slicer.vtkMRMLMarkupsFiducial = caller
        if self.tmpSphereSource:
            p1, p2 = np.empty(3), np.empty(3)
            markups.GetNthControlPointPositionWorld(0, p1)
            markups.GetNthControlPointPositionWorld(1, p2)
            center=(p1+p2)/2
            radius = np.linalg.norm(p1-p2)/2
            self.tmpSphereSource.SetCenter(center)
            self.tmpSphereSource.SetRadius(radius)
            self.tmpSphereSource.Update()
            slicer.util.forceRenderAllViews()




###############################################################""
    def save(self):
        dialog = qt.QFileDialog()
        filename = str(dialog.getSaveFileName(None,"Save detections","","JSON files (*.json)"))
        if filename:
            with open(filename, 'w') as f:
                json.dump({str(i): x for i,x in enumerate(self.detections)},f, indent=2)

    ##############################################################################################################   
    def reset(self):
        for node in self.spheres:
            slicer.mrmlScene.RemoveNode(node)
        self.spheres=[]
        for node in self.lines:
            slicer.mrmlScene.RemoveNode(node)
        self.lines=[]
        self.confidences.RemoveAllControlPoints()
        self.ostia.RemoveAllControlPoints()
        self.domes.RemoveAllControlPoints()
        if self.volNode:
            slicer.mrmlScene.RemoveNode(self.volNode)
        self.volNode : slicer.vtkMRMLScalarVolumeNode = None
        roiNode = slicer.util.getNodesByClass("vtkMRMLMarkupsROINode")
        for r in roiNode:
            slicer.mrmlScene.RemoveNode(r)
        
        self.removeAnnotationNodes()
        
        self.detections=[]

        self.tableWidget.setRowCount(0)
        self.tableWidget.setCurrentItem(None)
        self.onRowSelected(-1)
    
    def cleanup(self):
        """
        Called when the application closes and the module widget is destroyed.
        """
        slicer.mrmlScene.Clear(0)

    def enter(self):
        """
        Called each time the user opens this module.
        """
        # Make sure parameter node exists and observed
        self.initializeParameterNode()

    def exit(self):
        """
        Called each time the user opens a different module.
        """
        # Do not react to parameter node changes (GUI wlil be updated when the user enters into the module)
        return
        # self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self.updateGUIFromParameterNode)

    def onSceneStartClose(self, caller, event):
        """
        Called just before the scene is closed.
        """
        # Parameter node will be reset, do not use it anymore
        return
        # self.setParameterNode(None)

    def onSceneEndClose(self, caller, event):
        """
        Called just after the scene is closed.
        """
        # If this module is shown while the scene is closed then recreate a new parameter node immediately
        if self.parent.isEntered:
             self.initializeParameterNode()
        return

    def initializeParameterNode(self):
        """
        Ensure parameter node exists and observed.
        """
        # Parameter node stores all user choices in parameter values, node selections, etc.
        # so that when the scene is saved and reloaded, these settings are restored.
 
        return
