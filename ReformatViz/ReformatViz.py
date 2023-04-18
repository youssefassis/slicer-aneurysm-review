import logging
import os, json
from copy import deepcopy

import vtk, qt
import numpy as np

import slicer
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin

class ReformatViz(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "Reformat Aneurysm"  # TODO: make this more human readable by adding spaces
        self.parent.categories = ["Aneurysms"]
        self.parent.dependencies = []
        self.parent.contributors = ["Youssef Assis (Loria lab.)"]
        self.parent.acknowledgementText = "This module was developed by Youssef Assis"

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
        self.ui.load_volume_button.connect('clicked(bool)', self.load_files) # load volume
        self.ui.vol_visibility.stateChanged.connect(self.set_volume_visibility)
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


        #self.ui.cleanButton.connect('clicked(bool)', self.clearButton)


        # slices visibility
        self.ui.axial_visibility.stateChanged.connect(self.set_axial_slice_visibility)    
        
        # Slider: Rotate around axis
        self.ui.rotateXSlider.valueChanged.connect(self.rotateXSlider)
        self.ui.rotateYSlider.valueChanged.connect(self.rotateYSlider)
        # self.ui.rotateZSlider.valueChanged.connect(self.rotateZSlider)

        self.ui.ZoomSlider.valueChanged.connect(self.zoomAxial)

        # self.ui.LinecheckBox.stateChanged.connect(self.set_size_visibility)

        self.markups = None
        self.spheres = None
        self.appender_spheres = None
        self.appender_lines = None
        self.patient_path = None
        self.selected_detection = None
        self.detections = None
        
        self.org_zoom = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed').GetFieldOfView()

        self.last_x, self.last_y, self.last_z = 0, 0, 0

        columns = ['Confidence (%)', 'Size (mm)', 'Aneurysm ?']
        self.tableWidget = qt.QTableWidget()
        # self.tableWidget.setMaximumHeight(150)
        self.tableWidget.setColumnCount(len(columns))
        self.tableWidget.setHorizontalHeaderLabels(columns)
        self.tableWidget.horizontalHeader().setStretchLastSection(True)
        self.tableWidget.resizeColumnsToContents()
        self.tableWidget.setSelectionBehavior(self.tableWidget.SelectRows)

        self.layout_2 = qt.QGridLayout()
        self.layout_2.addWidget(self.tableWidget)
        self.ui.CollapsibleButton.setLayout(self.layout_2)

    def displayTable(self):
        if self.detections is None: 
            self.tableWidget.setRowCount(0)
            return

        self.tableWidget.setRowCount(len(self.detections))

        for idx in self.detections:
            # Confidence score
            confidence = str(round(self.detections[idx]['confidence']*100, 3))
            itm = qt.QTableWidgetItem(confidence)
            itm.setTextAlignment(4)
            self.tableWidget.setItem(int(idx), 0,  itm)

            # Size (mm)
            size = str(round(self.detections[idx]['radius']*2, 3))
            itm = qt.QTableWidgetItem(size)
            itm.setTextAlignment(4)
            self.tableWidget.setItem(int(idx), 1,  itm)

            self.tableWidget.clicked.connect(self.onRowSelected)

            cbox = qt.QCheckBox()
            # cbox.checked = int(annot['truth'])
            cell_widget = qt.QWidget()
            lay_out = qt.QHBoxLayout(cell_widget)
            lay_out.addWidget(cbox)
            lay_out.setAlignment(qt.Qt.AlignCenter)
            lay_out.setContentsMargins(0,0,0,0)
            cell_widget.setLayout(lay_out)
            self.tableWidget.setCellWidget(int(idx), 2, cell_widget)
            # cbox.clicked.connect(lambda checked, id=int(sphere_id): self.onStateChanged(checked, id))

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
        volNode = slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")
        if volNode:
            displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(volNode[0])
            rangeCT = [0] * 2
            displayNode.GetVolumePropertyNode().GetColor().GetRange(rangeCT)
            colorTransferFunction = vtk.vtkColorTransferFunction()
            colorTransferFunction.AddRGBPoint(rangeCT[0], color[0], color[1], color[2])
            colorTransferFunction.AddRGBPoint(rangeCT[1], color[0], color[1], color[2])
            displayNode.GetVolumePropertyNode().SetColor(colorTransferFunction)

    def onVolumeThresholdChanged(self, value):
        volNode = slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")
        if volNode:
            displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(volNode[0])
            volumePropertyNode = displayNode.GetVolumePropertyNode()

            # get the scalar opacity transfer function
            scalarOpacity = volumePropertyNode.GetVolumeProperty().GetScalarOpacity()

            # get the range of the scalar volume
            range = volNode[0].GetImageData().GetScalarRange()
            
            # set the threshold
            margin = range[1]/10
            upperThreshold = value
            scalarOpacity.RemoveAllPoints()
            scalarOpacity.AddPoint(upperThreshold, self.ui.volumeOpacity.value)
            scalarOpacity.AddPoint(max(range[0], upperThreshold-margin), 0.0)
            
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

    def onVolumeOpacityChanged(self):
        volNode = slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")
        if volNode:
            displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(volNode[0])
            scalarOpacity = displayNode.GetVolumePropertyNode().GetVolumeProperty().GetScalarOpacity()
            p = [0]*4
            scalarOpacity.GetNodeValue(1, p)
            p[1] = self.ui.volumeOpacity.value
            scalarOpacity.SetNodeValue(1, p)

    def zoomAxial(self, value):
        node = slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed')
        node.SetFieldOfView(self.org_zoom[0]*(1-value), self.org_zoom[1]*(1-value), self.org_zoom[2]*(1-value))
        node.UpdateMatrices()

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
    
    def set_axial_slice_visibility(self):
        layoutManager = slicer.app.layoutManager()
        controller = layoutManager.sliceWidget("Red").sliceController()
        controller.setSliceVisible(self.ui.axial_visibility.checked)

    def load_files(self):
        # save before loading
        dialog = qt.QFileDialog()
        dir_path = str(dialog.getExistingDirectory())
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
                if not displayNode: displayNode = volRenLogic.CreateDefaultVolumeRenderingNodes(self.volNode)
                volRenLogic.UpdateDisplayNodeFromVolumeNode(displayNode, self.volNode)
            
                min_val, max_val = self.volNode.GetImageData().GetScalarRange()
                self.ui.volumeThreshold.maximum = max_val
                self.ui.volumeThreshold.minimum = min_val
                self.ui.volumeThreshold.value = (max_val+min_val)/3

                # self.set_volume_color()
                self.ui.vol_visibility.checked = 1
            
            detection_files = sorted([f for f in os.listdir(dir_path) if f.endswith(".json")])
            if detection_files:
                self.load_detections(os.path.join(dir_path, detection_files[0]))
                self.create_spheres()
                self.displayTable()

                return 

                #self.prepare_slices()
                
    def load_detections(self, file_path):
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
        except:
            slicer.util.errorDisplay('Please choose the detections file with the right format.')
            return

        # sort by confidence score
        self.detections = {k: data[k] for k in sorted(data, key=lambda x:data[x]['confidence'], reverse=True) if data[k]['confidence']>=0.5}

    def create_spheres(self):
        if self.detections is None: return
        # self.appender_spheres = vtk.vtkAppendPolyData() if self.appender_spheres is None else self.appender_spheres
        self.appender_lines = vtk.vtkAppendPolyData() if self.appender_lines is None else self.appender_lines
        self.spheres = []
        self.lines = []
        # self.lines = [slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsLineNode") for i in self.detections]

        # Confidence Scores
        ConfidenceNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        ConfidenceNode.SetName("Confidences")
#        ConfidenceNode.GetDisplayNode().SetColor(0, 1, 0)
        ConfidenceNode.GetDisplayNode().SetVisibility(False) # self.ui.confidenceVisibility.checked

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

        for idx in self.detections:
            # Sphere
            sphere = vtk.vtkSphereSource()
            sphere.SetCenter(self.detections[str(idx)]['center'])
            sphere.SetRadius(self.detections[str(idx)]['radius'])
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
            line.SetPoint1(self.detections[str(idx)]['P1'])
            line.SetPoint2(self.detections[str(idx)]['P2'])
            line.SetResolution(30)
            self.appender_lines.AddInputConnection(line.GetOutputPort())
            self.appender_lines.Update()

            # lineNode.SetName("Size")
            # self.lines[int(idx)].GetMeasurement('length').SetEnabled(False)

            # Confidence scores
            n = ConfidenceNode.AddControlPoint(self.detections[str(idx)]['center'])
            ConfidenceNode.SetNthControlPointLabel(n, str(round(self.detections[str(idx)]['confidence'] * 100, 3))+" %")
            ConfidenceNode.SetLocked(1)

            # Confidence scores
            n = OstiumNode.AddControlPoint(self.detections[str(idx)]['P1'])
            OstiumNode.SetLocked(1)

            # Confidence scores
            n = DomeNode.AddControlPoint(self.detections[str(idx)]['P2'])
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
        if self.detections is None: return
        for i in range(len(self.detections)):
            sphere_name = f'Sphere_{i}'
            value = self.ui.SpheresVisibility.checked if i == self.selected_detection else False
            nodes = slicer.mrmlScene.GetNodesByName(sphere_name)
            for node in nodes:
                node.GetDisplayNode().SetVisibility(value)

    def set_spheres_color(self, color=None):
        if self.detections is None: return
        color = (color.red()/255, color.green()/255, color.blue()/255) if color is not None else (self.ui.SpheresColorPicker.color.red()/255, self.ui.SpheresColorPicker.color.green()/255, self.ui.SpheresColorPicker.color.blue()/255)
        for i in range(len(self.detections)):
            sphere_name = f'Sphere_{i}'
            nodes = slicer.mrmlScene.GetNodesByName(sphere_name)
            for node in nodes:
                node.GetDisplayNode().SetColor(color)

    def set_spheres_opacity(self):
        if self.detections is None: return
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

    def set_confidence_color(self, color=None):
        r, g, b = self.ui.ConfidenceColorPicker.color.red()/255, self.ui.ConfidenceColorPicker.color.green()/255, self.ui.ConfidenceColorPicker.color.blue()/255
        nodes = slicer.mrmlScene.GetNodesByName("Confidences")
        for node in nodes:
            pointListDisplayNode = node.GetDisplayNode()
            pointListDisplayNode.SetSelectedColor(r, g, b) # Set color to yellow
            pointListDisplayNode.SetColor(r, g, b) # Set color to yellow

    def set_confidence_visibility(self):
        nodes = slicer.mrmlScene.GetNodesByName("Confidences")
        #if self.selected_detection is None: return
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
        #if self.selected_detection is None: return
        for node in nodes:
            for i in range(node.GetNumberOfControlPoints()):
                if i == self.selected_detection:
                    node.SetNthControlPointVisibility(self.selected_detection, self.ui.MarkupsVisibility.checked)                    
                else:
                    node.SetNthControlPointVisibility(i, False)
            node.GetDisplayNode().SetVisibility(self.ui.MarkupsVisibility.checked)

        nodes = slicer.mrmlScene.GetNodesByName("Dome")
        #if self.selected_detection is None: return
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
        if self.selected_detection is None and self.detecitons is None: return
        
        A = np.array(self.detections[str(self.selected_detection)]["P1"])
        B = np.array(self.detections[str(self.selected_detection)]["P2"])

        V = B - A
        N = np.cross(V, np.array([0, 0, 1]))
        newNormal = N / np.linalg.norm(N)
        reformatLogic = slicer.modules.reformat.logic()

        reformatLogic.SetSliceNormal(slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed'), newNormal)
        reformatLogic.SetSliceOrigin(slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeRed'), (A+B)/2)

        # reformatLogic.SetSliceNormal(slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeGreen'), newNormal)
        reformatLogic.SetSliceOrigin(slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeGreen'), (A+B)/2)
        
        # reformatLogic.SetSliceNormal(slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeYellow'), newNormal)
        reformatLogic.SetSliceOrigin(slicer.mrmlScene.GetNodeByID('vtkMRMLSliceNodeYellow'), (A+B)/2)

        self.ui.ZoomSlider.value = 0.94


        # camera focal point 
        camera = slicer.app.layoutManager().threeDWidget(0).threeDView().displayableManagerByClassName('vtkMRMLCameraDisplayableManager').GetCameraWidget().GetCameraNode()
        camera.SetFocalPoint(((A+B)/2).tolist())




    def remove_nodes_by_type(self, node_type_name):
        volumeNodes = slicer.util.getNodesByClass(node_type_name)
        for node in volumeNodes:
            slicer.mrmlScene.RemoveNode(node)

    def set_volume_visibility(self):
        volNode = slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")
        if volNode:
            displayNode = slicer.modules.volumerendering.logic().GetFirstVolumeRenderingDisplayNode(volNode[0])
            if displayNode: displayNode.SetVisibility(self.ui.vol_visibility.checked)
    
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
        self.cleanup()

    def remove_nodes_by_name(self, name):
        nodes = slicer.mrmlScene.GetNodesByName(name)
        for node in nodes:
            slicer.mrmlScene.RemoveNode(node)

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
