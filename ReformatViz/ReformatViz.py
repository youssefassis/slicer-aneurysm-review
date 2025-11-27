import logging
import os, json
from copy import deepcopy

import numpy as np
import math
import functools

import vtk, qt
from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction


import slicer
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin


eps=np.finfo(np.float64).resolution

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


        self.ui.saveButton.clicked.connect(self.save)
        self.ui.cleanButton.connect('clicked(bool)', self.clearButton)


        # slices visibility
        self.ui.axialVisibility.stateChanged.connect(self.set_axial_slice_visibility)    
        
        # Slider: Rotate around axis
        self.ui.rotateAxisSlider.valueChanged.connect(self.rotateAxisSlider)
        self.ui.centerCamera.clicked.connect(self.centerCamera)
        
        # slice thickness
        self.ui.thicknessSlider.valueChanged.connect(self.thicknessSlider)

        # self.ui.LinecheckBox.stateChanged.connect(self.set_size_visibility)

        self.markups = None
        self.spheres = None
        self.appender_spheres = None
        self.appender_lines = None
        self.patient_path = None
        self.selected_detection = -1
        self.detections = []
        self.volNode=None
        self.volProp=None
        self.scalarOpacity=makeScalarOpacity()
        self.margin=20

        self.aneAxis=np.array([0.0,0.0,1.0])

        self.last_x, self.last_y, self.last_z = 0, 0, 0

        columns = ['Confidence (%)', 'Size (mm)', 'Aneurysm ?']
        self.tableWidget = qt.QTableWidget()
        # self.tableWidget.setMaximumHeight(150)
        self.tableWidget.setColumnCount(len(columns))
        self.tableWidget.setHorizontalHeaderLabels(columns)
        self.tableWidget.horizontalHeader().setStretchLastSection(True)
        self.tableWidget.resizeColumnsToContents()
        self.tableWidget.setSelectionBehavior(self.tableWidget.SelectRows)
        self.tableWidget.setSelectionMode(self.tableWidget.SingleSelection)

        self.layout_2 = qt.QGridLayout()
        self.layout_2.addWidget(self.tableWidget)
        self.ui.CollapsibleButton.setLayout(self.layout_2)

    def displayTable(self):
        self.tableWidget.setRowCount(len(self.detections))

        for idx,d in enumerate(self.detections):
            # Confidence score
            confidence = round(d['confidence']*100, 3)
            itm = qt.QTableWidgetItem(str(confidence))
            itm.setTextAlignment(4)
            self.tableWidget.setItem(int(idx), 0,  itm)

            # Size (mm)
            size = round(d['radius']*2, 3)
            itm = qt.QTableWidgetItem(str(size))
            itm.setTextAlignment(4)
            self.tableWidget.setItem(int(idx), 1,  itm)

            self.tableWidget.clicked.connect(self.onRowSelected)

            cbox = qt.QCheckBox()
            cbox.checked = int(d['aneurysm'])
            cell_widget = qt.QWidget()
            lay_out = qt.QHBoxLayout(cell_widget)
            lay_out.addWidget(cbox)
            lay_out.setAlignment(qt.Qt.AlignCenter)
            lay_out.setContentsMargins(0,0,0,0)
            cell_widget.setLayout(lay_out)
            self.tableWidget.setCellWidget(int(idx), 2, cell_widget)
            self.tableWidget.setCurrentItem(self.tableWidget.item(self.selected_detection,0))
            cbox.stateChanged.connect(functools.partial(self.confirmAneurysm,det=d))

    
    def confirmAneurysm(self, state, det):
        det['aneurysm']=bool(state)

    def onRowSelected(self, item):
        self.selected_detection  = item.row() if not isinstance(item, int) else item

        self.set_spheres_visibility()

        # self.set_Lines_visibility()
        self.set_confidence_color()
        self.set_confidence_visibility()
        self.set_markups_color()
        self.set_markups_visibility()
        self.prepare_slices()

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

    # def onVolumeThresholdChanged(self, value):
    #     volNode = slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")
    #     if volNode:
    #         displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(volNode[0])
    #         volumePropertyNode = displayNode.GetVolumePropertyNode()

    #         # get the scalar opacity transfer function
    #         scalarOpacity = volumePropertyNode.GetVolumeProperty().GetScalarOpacity()
    #         p1, p2, p3 = [0]*4, [0]*4, [0]*4
    #         scalarOpacity.GetNodeValue(1, p1)
    #         scalarOpacity.GetNodeValue(2, p2)
    #         scalarOpacity.GetNodeValue(3, p3)
    #         d = value - p2[0]
    #         p1[0] += d
    #         p2[0] += d
    #         p3[0] += d

    #         p1[1], p2[1], p3[1] = 0, self.ui.volumeOpacity.value, 0
    #         scalarOpacity.SetNodeValue(1,p1)
    #         scalarOpacity.SetNodeValue(2,p2)
    #         scalarOpacity.SetNodeValue(3,p3)

    def onVolumeOpacityChanged(self,value):
        self.updateScalarOpacity()

    def rotateXSlider(self, value):
        node = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed')
        sliceToRas = node.GetSliceToRAS()
        transform=vtk.vtkTransform()
        transform.SetMatrix(sliceToRas)
        transform.RotateX(self.last_x-value)
        self.last_x = value
        sliceToRas.DeepCopy(transform.GetMatrix())
        node.UpdateMatrices()

    def rotateYSlider(self, value):
        node = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed')
        sliceToRas = node.GetSliceToRAS()
        transform=vtk.vtkTransform()
        transform.SetMatrix(sliceToRas)
        transform.RotateY(self.last_y-value)
        self.last_y = value
        sliceToRas.DeepCopy(transform.GetMatrix())
        node.UpdateMatrices()

    def rotateZSlider(self, value):
        node = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed')
        sliceToRas = node.GetSliceToRAS()
        transform=vtk.vtkTransform()
        transform.SetMatrix(sliceToRas)
        transform.RotateZ(self.last_z-value)
        self.last_z = value
        sliceToRas.DeepCopy(transform.GetMatrix())
        node.UpdateMatrices()

    def rotateAxisSlider(self, value):
        node = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed')
        if node is not None:
            if self.detections and self.selected_detection >= 0:
                self.detections[self.selected_detection]['viewAngle']=value
            angle=value/180*np.pi
            aneNormal = self.aneRz.T@np.array([math.cos(angle),math.sin(angle),0])
            #aneTrans = np.cross(self.aneAxis,aneNormal)
            #node.SetSliceToRASByNTP(*aneNormal,*aneTrans,*self.aneOrigin,0)
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
        #sliceToRas = node.GetSliceToRAS()
        #transform=vtk.vtkTransform()
        #transform.SetMatrix()
        #transform.RotateZ(self.last_z-value)
        #self.last_z = value
        #sliceToRas.DeepCopy(transform.GetMatrix())
        # node.UpdateMatrices()
    
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
            if nii_volume_files:
                #if len(nii_volume_files)>1: slicer.util.errorDisplay('There too many volume files in that folder. Loading the first one.')
                self.remove_existing_volumes() # remove existing volume
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

            detection_files = sorted([f for f in os.listdir(dir_path) if f.endswith(".json")])
            if detection_files:
                self.load_detections(os.path.join(dir_path, detection_files[0]))
                self.create_spheres()
                self.displayTable()
                
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
            slicer.util.errorDisplay('Please choose the detections file with the right format.')
            return

        # sort by confidence score
        self.detections=[d for d in data.values()]

        # add missing info
        for d in self.detections:
            if not 'confidence' in d:
                d['confidence'] = 1
            if not 'center' in d:
                P1=np.array(d['P1'])
                P2=np.array(d['P2'])
                C=(P1+P2)/2
                d['center'] = C.tolist()
            if not 'radius' in d:
                P1=np.array(d['P1'])
                P2=np.array(d['P2'])
                R=np.linalg.norm(P1-P2)
                d['radius'] = R 
            if not 'viewAngle' in d:
                d['viewAngle'] = 0
            if not 'aneurysm' in d:
                d['aneurysm'] = False


        #self.detections = sorted([d for d in self.detections if d['confidence']>=0.5], key=lambda x:x['confidence'], reverse=True)
        self.detections = sorted(self.detections, key=lambda x:x['confidence'], reverse=True)
        

    
    def create_spheres(self):
        if self.detections is None or len(self.detections) == 0: return
        # self.appender_spheres = vtk.vtkAppendPolyData() if self.appender_spheres is None else self.appender_spheres
        self.appender_lines = vtk.vtkAppendPolyData() if self.appender_lines is None else self.appender_lines
        self.spheres = []
        self.lines = []
        # self.lines = [slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsLineNode") for i in self.detections]

        if 'confidence' in self.detections[0]:
            # Confidence Scores
            ConfidenceNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
            ConfidenceNode.SetName("Confidences")
    #        ConfidenceNode.GetDisplayNode().SetColor(0, 1, 0)
            ConfidenceNode.GetDisplayNode().SetVisibility(False) # self.ui.confidenceVisibility.checked
        else:
            ConfidenceNode=None

        # Ostium Point
        OstiumNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        OstiumNode.SetName("Ostium")
#        OstiumNode.GetDisplayNode().SetColor(0, 1, 0)
        OstiumNode.GetDisplayNode().SetVisibility(False)
        # Dome Point
        DomeNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        DomeNode.SetName("Dome")
#        DomeNode.GetDisplayNode().SetColor(0, 1, 0)
        DomeNode.GetDisplayNode().SetVisibility(False)

        for idx,d in enumerate(self.detections):
            # Sphere
            sphere = vtk.vtkSphereSource()
            sphere.SetCenter(d['center'])
            sphere.SetRadius(d['radius'])
            sphere.SetPhiResolution(30)
            sphere.SetThetaResolution(30)
            sphere.Update()

            # Assign a unique name to each sphere
            self.create_model(sphere, name=f'Sphere_{idx}')

            # node = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLModelNode', sphere_name)
            # node.SetAndObservePolyData(sphere.GetOutput())
            # # self.appender_spheres.AddInputConnection(node.GetPolyDataConnection())
            # self.spheres.append(node)


            # self.appender_spheres.AddInputConnection(sphere.GetOutputPort())
            # self.spheres.append(sphere)

            # Line
            line = vtk.vtkLineSource()
            line.SetPoint1(d['P1'])
            line.SetPoint2(d['P2'])
            line.SetResolution(30)
            self.appender_lines.AddInputConnection(line.GetOutputPort())
            self.appender_lines.Update()

            # lineNode.SetName("Size")
            # self.lines[int(idx)].GetMeasurement('length').SetEnabled(False)

            if ConfidenceNode is not None:
               # Confidence scores
                n = ConfidenceNode.AddControlPoint(d['center'])
                ConfidenceNode.SetNthControlPointLabel(n, str(round(d['confidence'] * 100, 3))+" %")
                ConfidenceNode.SetLocked(1)

            # Confidence scores
            n = OstiumNode.AddControlPoint(d['P1'])
            OstiumNode.SetLocked(1)

            # Confidence scores
            n = DomeNode.AddControlPoint(d['P2'])
            DomeNode.SetLocked(1)

        
        # Lines: main axes
        self.appender_lines.Update()
        self.create_model(self.appender_lines, name="Lines")
        self.set_lines_color()
        self.set_lines_visibility()

        # Spheres
        # self.appender_spheres.Update()
        # self.create_model(self.appender_spheres, name="Spheres")
        
        self.set_spheres_color()
        self.set_spheres_visibility()
        
    def set_spheres_visibility(self):
        for i in range(len(self.detections)):
            sphere_name = f'Sphere_{i}'
            value = self.ui.SpheresVisibility.checked if i == self.selected_detection else False
            nodes = slicer.mrmlScene.GetNodesByName(sphere_name)
            for node in nodes:
                node.GetDisplayNode().SetVisibility(value)

    def set_spheres_color(self, color=None):
        color = (color.red()/255, color.green()/255, color.blue()/255) if color is not None else (self.ui.SpheresColorPicker.color.red()/255, self.ui.SpheresColorPicker.color.green()/255, self.ui.SpheresColorPicker.color.blue()/255)
        for i in range(len(self.detections)):
            sphere_name = f'Sphere_{i}'
            nodes = slicer.mrmlScene.GetNodesByName(sphere_name)
            for node in nodes:
                node.GetDisplayNode().SetColor(color)

    def set_spheres_opacity(self):
        for i in range(len(self.detections)):
            sphere_name = f'Sphere_{i}'
            nodes = slicer.mrmlScene.GetNodesByName(sphere_name)
            for node in nodes:
                node.GetDisplayNode().SetOpacity(self.ui.SpheresOpacity.value)

    def create_model(self, model, name):
        #self.remove_nodes_by_name(name)
        node = slicer.modules.models.logic().AddModel(model.GetOutput())
        node.SetName(name)
        node.GetDisplayNode().SetVisibility2D(True)
        node.GetDisplayNode().SetSliceIntersectionThickness(2)
        if "Sphere" in name: node.GetDisplayNode().SetOpacity(self.ui.SpheresOpacity.value)
        return node

    def set_confidence_color(self, color=None):
        r, g, b = self.ui.ConfidenceColorPicker.color.red()/255, self.ui.ConfidenceColorPicker.color.green()/255, self.ui.ConfidenceColorPicker.color.blue()/255
        nodes = slicer.mrmlScene.GetNodesByName("Confidences")
        for node in nodes:
            pointListDisplayNode = node.GetDisplayNode()
            pointListDisplayNode.SetSelectedColor(r, g, b) # Set color to yellow
            pointListDisplayNode.SetColor(r, g, b) # Set color to yellow

    def set_confidence_visibility(self):
        nodes = slicer.mrmlScene.GetNodesByName("Confidences")
        for node in nodes:
            for i in range(node.GetNumberOfControlPoints()):
                if i == self.selected_detection:
                    node.SetNthControlPointVisibility(self.selected_detection, self.ui.ConfidenceVisibility.checked)                    
                else:
                    node.SetNthControlPointVisibility(i, False)
            node.GetDisplayNode().SetVisibility(self.ui.ConfidenceVisibility.checked)

    def set_markups_color(self, color=None):
        r, g, b = self.ui.MarkupsColorPicker.color.red()/255, self.ui.MarkupsColorPicker.color.green()/255, self.ui.MarkupsColorPicker.color.blue()/255
        nodes = slicer.mrmlScene.GetNodesByName("Ostium")
        for node in nodes:
            pointListDisplayNode = node.GetDisplayNode()
            pointListDisplayNode.SetSelectedColor(r, g, b) # Set color to yellow
            pointListDisplayNode.SetColor(r, g, b) # Set color to yellow
        nodes = slicer.mrmlScene.GetNodesByName("Dome")
        for node in nodes:
            pointListDisplayNode = node.GetDisplayNode()
            pointListDisplayNode.SetSelectedColor(r, g, b) # Set color to yellow
            pointListDisplayNode.SetColor(r, g, b) # Set color to yellow

    def set_markups_visibility(self):
        nodes = slicer.mrmlScene.GetNodesByName("Ostium")
        for node in nodes:
            for i in range(node.GetNumberOfControlPoints()):
                if i == self.selected_detection:
                    node.SetNthControlPointVisibility(self.selected_detection, self.ui.MarkupsVisibility.checked)                    
                else:
                    node.SetNthControlPointVisibility(i, False)
            node.GetDisplayNode().SetVisibility(self.ui.MarkupsVisibility.checked)

        nodes = slicer.mrmlScene.GetNodesByName("Dome")
        for node in nodes:
            for i in range(node.GetNumberOfControlPoints()):
                if i == self.selected_detection:
                    node.SetNthControlPointVisibility(self.selected_detection, self.ui.MarkupsVisibility.checked)                    
                else:
                    node.SetNthControlPointVisibility(i, False)
            node.GetDisplayNode().SetVisibility(self.ui.MarkupsVisibility.checked)

    # def set_spheres_visibility(self):
    #     nodes = slicer.mrmlScene.GetNodesByName("Spheres")
    #     for node in nodes:
    #         node.GetDisplayNode().SetVisibility(self.ui.SpheresVisibility.checked)

    def set_lines_color(self, color=None):
        color = (color.red()/255, color.green()/255, color.blue()/255) if color is not None else (self.ui.LinesColorPicker.color.red()/255, self.ui.LinesColorPicker.color.green()/255, self.ui.LinesColorPicker.color.blue()/255) 
        nodes = slicer.mrmlScene.GetNodesByName("Lines")
        for node in nodes:
            node.GetDisplayNode().SetColor(color)


    # def draw_lines(self):
    #     nb_spheres = self.markups.GetNumberOfControlPoints()//2
    #     for idx in range(nb_spheres):
    #         # Create a line node
    #         lineNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsLineNode")
    
    #         pos1 = self.markups.GetNthControlPointPosition(idx*2)
    #         pos2 = self.markups.GetNthControlPointPosition(idx*2+1)
    #         # Set the line endpoints
    #         lineNode.SetLineStartPosition(pos1)
    #         lineNode.SetLineEndPosition(pos2)0
    #         lineNode.SetName("Size")
            
    #         lineNode.SetLocked(True)
    #         #lineNode.GetMeasurement('length').SetEnabled(False)
    
    def prepare_slices (self):
        if not self.detections or self.selected_detection < 0: 
            self.axeAxis=np.array([0.0,1.0,0.0])
            angleValue=0
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

    def centerCamera(self):
        # camera focal point 
        camera = slicer.app.layoutManager().threeDWidget(0).threeDView().displayableManagerByClassName('vtkMRMLCameraDisplayableManager').GetCameraWidget().GetCameraNode()

        # angle=self.ui.rotateAxisSlider.value/180*np.pi
        # aneNormal = self.aneRz.T@np.array([math.cos(angle),math.sin(angle),0])
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

    def remove_nodes_by_type(self, node_type_name):
        volumeNodes = slicer.util.getNodesByClass(node_type_name)
        for node in volumeNodes:
            slicer.mrmlScene.RemoveNode(node)

    def set_volume_visibility(self):
        if self.volNode:
            displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(self.volNode)
            if displayNode:
                displayNode.SetVisibility(self.ui.vol_visibility.checked)
    
    def set_volume_translucent(self):
        self.updateScalarOpacity()


    def ResetCamera(self):
        camera = slicer.util.getNode('Camera').GetCamera()
        camera.SetFocalPoint([0,0,0])
        camera.SetPosition([0, 500, 0])
        camera.SetViewUp([0, 0, 1])

    def remove_existing_volumes(self):
        self.remove_nodes_by_type("vtkMRMLScalarVolumeNode") # remove existing volume


    def set_lines_visibility(self):
        nodes = slicer.mrmlScene.GetNodesByName("Lines")
        for node in nodes:
            node.GetDisplayNode().SetVisibility(self.ui.LinesVisibility.checked)

    def clearButton(self):
        self.tableWidget.setCurrentItem(None)
        self.onRowSelected(-1)
        #self.cleanup()

    def remove_nodes_by_name(self, name):
        nodes = slicer.mrmlScene.GetNodesByName(name)
        for node in nodes:
            slicer.mrmlScene.RemoveNode(node)

    def save(self):
        dialog = qt.QFileDialog()
        filename = str(dialog.getSaveFileName(None,"Save detections","","JSON files (*.json)"))
        if filename:
            with open(filename, 'w') as f:
                json.dump({str(i): x for i,x in enumerate(self.detections)},f, indent=2)

    ##############################################################################################################   
    def cleanup(self):
        """
        Called when the application closes and the module widget is destroyed.
        """
        slicer.mrmlScene.Clear(0)
        self.removeObservers()

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
        # if self.parent.isEntered:
        #     self.initializeParameterNode()
        return

    def initializeParameterNode(self):
        """
        Ensure parameter node exists and observed.
        """
        # Parameter node stores all user choices in parameter values, node selections, etc.
        # so that when the scene is saved and reloaded, these settings are restored.
        return
