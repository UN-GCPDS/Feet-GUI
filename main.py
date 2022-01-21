# GCPDS - Universidad Nacional de Colombia
# Proyecto caracterización termográfica de extremidades inferiores durante aplicación de anestesia epidural
# Mayo de 2021
# Disponible en https//:github.com/blotero/FEET-GUI

import os
import re
from pathlib import Path
import sys
import matplotlib.pyplot as plt
import numpy as np
import cv2
from PySide2.QtWidgets import QApplication, QMainWindow, QFileDialog 
from PySide2.QtCore import QFile, QObject, SIGNAL, QDir, QTimer
from PySide2.QtUiTools import QUiLoader 
from segment import ImageToSegment, SessionToSegment, remove_small_objects
from manualseg import manualSeg
from temperatures import mean_temperature
from scipy.interpolate import make_interp_spline 
import cv2
from PySide2.QtWidgets import *
from PySide2.QtCore import *
from PySide2.QtGui import *
from datetime import datetime
import tflite_runtime.interpreter as tflite


class UnauthorizedException(Exception):
    def __init__(self, URL):
        self.URL = URL
        self.message = "Error syncing local information to remote repository in: " + str(URL)
        super.__init__(self.message)


class NotImplementedError(Exception):
    def __init__(self):
        self.message = "This feature has not been implemented" 
        super.__init__(self.message)

    
class RemotePullException(Exception):
    def __init__(self, repoURL):
        self.message = "Error pulling new changes into local DB from origin: " + str(repoURL)
        super.__init__(self.message)
    

class Window(QMainWindow):
    def __init__(self):
        super(Window, self).__init__()
        self.loadUI()        
        self.imgs = []
        self.subj = []
        self.make_connect()
        self.inputExists = False
        self.defaultDirectoryExists = False
        self.isSegmented = False
        self.files = None
        self.temperaturesWereAcquired = False
        self.scaleModeAuto = True
        self.modelsPathExists = False
        self.model = 'default_model.tflite'
        self.fullScreen = True
        #Loading segmentation models
        self.s2s = SessionToSegment()
        self.i2s = ImageToSegment()
        self.s2s.setModel(self.model)
        self.i2s.setModel(self.model)
        self.s2s.loadModel()
        self.i2s.loadModel()
        self.ui_window.loadedModelLabel.setText(self.model)
        self.setupCamera()
        self.sessionIsCreated = False
        self.driveURL = None
        self.rcloneIsConfigured = False
        self.repoUrl = 'https://github.com/blotero/FEET-GUI.git' 
        self.digits_model = tflite.Interpreter(model_path = './digits_recognition.tflite')
        self.digits_model.allocate_tensors()
        
    def predict_number(self,image):
        
        image_2 = cv2.resize(image, (28, 28), interpolation = cv2.INTER_NEAREST)
        image_2 = image_2[:,:,0]
        image_2 = np.where(image_2>0.2, 1, 0)
        image_2 = np.expand_dims(image_2, -1)
        image_2 = np.expand_dims(image_2, 0)
        input_details = self.digits_model.get_input_details()
        output_details = self.digits_model.get_output_details()

        input_shape = input_details[0]['shape']
        input_data = np.float32(image_2)

        self.digits_model.set_tensor(input_details[0]['index'], input_data)

        self.digits_model.invoke()  # predict

        output_data = self.digits_model.get_tensor(output_details[0]['index'])

        return np.argmax(output_data)  
        
     
    def extract_scales(self, x):
        lower_digit_1 = self.predict_number(x[446: 466, 576: 590])
        lower_digit_2 = self.predict_number(x[446: 466, 590: 604])
        lower_digit_3 = self.predict_number(x[446: 466, 610: 624])
        
        upper_digit_1 = self.predict_number(x[14: 34, 576: 590])
        upper_digit_2 = self.predict_number(x[14: 34, 590: 604])
        upper_digit_3 = self.predict_number(x[14: 34, 610: 624])

        lower_bound = lower_digit_1*10 + lower_digit_2 + lower_digit_3*0.1
        upper_bound = upper_digit_1*10 + upper_digit_2 + upper_digit_3*0.1

        return lower_bound, upper_bound

    def extract_multiple_scales(self, X):
        scales = []
        for i in range(X.shape[0]):
            scales.append(self.extract_scales(X[i]))
            
        return scales

    def setupCamera(self):
        """Initialize camera.
        """
        self.capture = cv2.VideoCapture(0)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        self.timer = QTimer()
        self.timer.timeout.connect(self.displayFrame)
        self.timer.start(30)


    def displayFrame(self):
        try:
            self.ret, self.frame = self.capture.read()
            self.frame = cv2.cvtColor(self.frame, cv2.COLOR_BGR2RGB)
            # image = qimage2ndarray.array2qimage(self.frame)
            self.image = QImage(self.frame, self.frame.shape[1], self.frame.shape[0], 
                        self.frame.strides[0], QImage.Format_RGB888)
            self.ui_window.inputImg.setPixmap(QPixmap.fromImage(self.image))
        except:
            pass

    def loadUI(self):
        loader = QUiLoader()        
        path = os.fspath(Path(__file__).resolve().parent / "form.ui")
        ui_file = QFile(path)
        ui_file.open(QFile.ReadOnly)
        self.ui_window = loader.load(ui_file, self)
        self.ui_window.showFullScreen()
        ui_file.close()
    
    def capture_image(self):
        if (not self.sessionIsCreated):
            self.createSession()
        
        if len(os.listdir(self.session_dir)) <= 1:
            image_number = len(os.listdir(self.session_dir))
        else:
            image_number = 5*len(os.listdir(self.session_dir)) - 5
        
        self.save_name = f't{image_number}.jpg'
        plt.imsave(os.path.join(self.session_dir, self.save_name), self.frame)
        self.ui_window.outputImg.setPixmap(QPixmap.fromImage(self.image))
        self.ui_window.imgName.setText(self.save_name[:-4])
        
        if self.ui_window.autoScaleCheckBox.isChecked():
            # Read and set the temperature range:
            temp_scale = self.extract_scales(self.frame)
            self.ui_window.minSpinBox.setValue(temp_scale[0])
            self.ui_window.maxSpinBox.setValue(temp_scale[1])
            self.messagePrint(f"Escala leida: {temp_scale}. Por favor verifique que sea la correcta y corrijala en caso de que no lo sea.")
    def createSession(self):
        """
        Creates a new session, including a directory in ./outputs/<session_dir> with given input parameters
        from GUI
        The session is named as the current timestamp if current session_dir is null
        """
        self.name = self.ui_window.nameField.text()
        self.dir_name = self.name.replace(' ','_')
        if self.dir_name == '':
            today = datetime.today()
            self.dir_name = today.strftime("%Y-%m-%d_%H:%M")            
        try:
            self.session_dir = os.path.join('outputs',self.dir_name)
            os.mkdir(self.session_dir)
            self.sessionIsCreated = True
            self.messagePrint("Sesión " + self.session_dir + " creada exitosamente." )
        except:
            self.messagePrint("Fallo al crear la sesión. Lea el manual de ayuda para encontrar solución, o reporte bugs al " + self.bugsURL)
        
    def syncLocalInfoToDrive(self):
        """
        Syncs info from the output directory to the configured sync path
        """
        self.messagePrint("Sincronizando información al repositorio remoto...")
        try:
            status = os.system("rclone copy outputs drive:")
            self.messagePrint("Sincronizando información al repositorio remoto...")
            if status == 0:
                if self.rcloneIsConfigured:
                    raise UnauthorizedException(self.driveURL)
                raise Exception("Error de sincronización")
            self.messagePrint("Se ha sincronizado exitosamente la información")
        except UnauthorizedException as ue:
            self.messagePrint("Error de autorización durante la sincronización. Dirígase a Ayuda > Acerca de para más información.")
            print(ue)
        except Exception as e:
            self.messagePrint("Error al sincronizar la información al repositorio. Verifique que ha seguido los pasos de instalación y configuración de rclone. Para más información, dirígase a Ayuda > Acerca de.")
            print(e)

    def repoConfigDialog(self):
        """
        Shows a dialog window for first time configuring the remote repository sync for the current device
        """
        raise NotImplementedError()

    def make_connect(self):
        QObject.connect(self.ui_window.actionCargar_imagen, SIGNAL ('triggered()'), self.openImage)
        QObject.connect(self.ui_window.actionCargar_carpeta , SIGNAL ('triggered()'), self.openFolder)
        QObject.connect(self.ui_window.actionCargar_modelos , SIGNAL ('triggered()'), self.getModelsPath)
        QObject.connect(self.ui_window.actionPantalla_completa , SIGNAL ('triggered()'), self.toggleFullscreen)
        QObject.connect(self.ui_window.actionSalir , SIGNAL ('triggered()'), self.exit_)
        QObject.connect(self.ui_window.actionC_mo_usar , SIGNAL ('triggered()'), self.howToUse)
        QObject.connect(self.ui_window.actionUpdate , SIGNAL ('triggered()'), self.updateSoftware)
        QObject.connect(self.ui_window.actionRepoSync , SIGNAL ('triggered()'), self.syncLocalInfoToDrive)
        QObject.connect(self.ui_window.actionRepoConfig , SIGNAL ('triggered()'), self.repoConfigDialog)
        QObject.connect(self.ui_window.segButtonImport, SIGNAL ('clicked()'), self.segment)
        #QObject.connect(self.ui_window.tempButton, SIGNAL ('clicked()'), self.temp_extract)
        QObject.connect(self.ui_window.tempButtonImport, SIGNAL ('clicked()'), self.temp_extract)
        QObject.connect(self.ui_window.captureButton, SIGNAL ('clicked()'), self.capture_image)
        QObject.connect(self.ui_window.nextImageButton , SIGNAL ('clicked()'), self.nextImage)
        QObject.connect(self.ui_window.previousImageButton , SIGNAL ('clicked()'), self.previousImage)
        QObject.connect(self.ui_window.reportButton , SIGNAL ('clicked()'), self.exportReport)
        QObject.connect(self.ui_window.loadModelButton , SIGNAL ('clicked()'), self.toggleModel)
        QObject.connect(self.ui_window.createSession, SIGNAL ('clicked()'), self.createSession)
        QObject.connect(self.ui_window.segButton, SIGNAL ('clicked()'), self.segment_capture)
    
    def segment_capture(self):
        self.messagePrint("Segmentando imagen...")
        self.i2s.setModel(self.model)
        self.i2s.setPath(os.path.join(self.session_dir,self.save_name))
        self.i2s.loadModel()
        self.i2s.extract()
        threshold =  0.5
        img = plt.imread(os.path.join(self.session_dir, self.save_name))/255
        Y = self.i2s.Y_pred
        Y = Y / Y.max()
        Y = np.where( Y >= threshold  , 1 , 0)
        self.Y =remove_small_objects( Y[0])     #Eventually required by temp_extract
        Y = cv2.resize(Y[0], (img.shape[1],img.shape[0]), interpolation = cv2.INTER_NEAREST) # Resize the prediction to have the same dimensions as the input 
        if self.ui_window.rainbowCheckBoxImport.isChecked():
            cmap = 'rainbow'
        else:
            cmap = 'gray'
        # plt.figure()
        # plt.plot(Y*img[:,:,0])
        # plt.savefig("outputs/output.jpg")
        plt.imsave("outputs/output.jpg" , Y*img[:,:,0] , cmap=cmap)
        self.ui_window.outputImg.setPixmap("outputs/output.jpg")
        self.isSegmented = True
        self.messagePrint("Imagen segmentada exitosamente")

    def setDefaultConfigSettings(self, model_dir, session_dir):
        self.config = {'models_directory': model_dir,
                'session_directory': session_dir }

    def updateUserConfiguration(self):
        self.modelsPath = self.config['models_directory']
        self.defaultDirectory = self.config['session_directory']

    def messagePrint(self, message):
        #INPUT: string to print
        #OUTPUT: none
        #ACTION: generate out.html file and refresh it in Messages QTextArea
        log_path = "outputs/logs.html"
        out_file = open(log_path , "w")
        out_file.write(message)
        out_file.close()
        self.ui_window.textBrowser.setSource(log_path)
        self.ui_window.textBrowser.reload()

    def findImages(self):
        self.fileList = []  #Absolute paths
        self.files = []     #Relative paths
        self.outfiles=[]    #Relative path to output files
        for root, dirs, files in os.walk(self.defaultDirectory):
            for file in files:
                if (file.endswith(".jpg")):
                    self.fileList.append(os.path.join(root,file))
                    self.files.append(file) 
                    self.outfiles.append("outputs/" + file) #Creating future output file names
        self.imageQuantity = len(self.fileList)
        self.imageIndex = 0
        self.sortFiles()
        self.ui_window.inputLabel.setText(self.files[self.imageIndex])

    def sortFiles(self):
        """Sort file list to an alphanumeric reasonable sense"""         
        convert = lambda text: int(text) if text.isdigit() else text 
        alphanum_key = lambda key: [ convert(c) for c in re.split('([0-9]+)', key) ] 
        self.fileList =  sorted(self.fileList, key = alphanum_key)
        self.files =  sorted(self.files, key = alphanum_key)

    def getTimes(self):
        """
        Converts standarized names of file list into a list of 
        integers with time capture in minutes
        """
        if (type(self.fileList)==str):
            self.timeList =  int(self.fileList).rsplit(".")[0][1:]
        elif type(self.fileList)==list:    
            out_list = []
            for i in range(len(self.fileList)):
                out_list.append(int(self.files[i].rsplit(".")[0][1:]))
            self.timeList =  out_list
        else:
            return None

    def nextImage(self):
        if self.imageIndex < len(self.fileList)-1:
            self.imageIndex += 1
            self.ui_window.inputImgImport.setPixmap(self.fileList[self.imageIndex])
            self.opdir = self.fileList[self.imageIndex]
            self.ui_window.inputLabel.setText(self.files[self.imageIndex])

            if self.sessionIsSegmented:
                #Sentences to display next output image if session was already
                #segmented
                self.showOutputImageFromSession()
                if self.temperaturesWereAcquired:
                    self.messagePrint("La temperatura media es: " + str(self.meanTemperatures[self.imageIndex]))
                    self.ui_window.temperatureLabelImport.setText(str(np.round(self.meanTemperatures[self.imageIndex], 3)))
                
    def previousImage(self):
        if self.imageIndex >= 1:
            self.imageIndex -= 1
            self.ui_window.inputImgImport.setPixmap(self.fileList[self.imageIndex])
            self.opdir = self.fileList[self.imageIndex]
            self.ui_window.inputLabel.setText(self.files[self.imageIndex])

            if self.sessionIsSegmented:
                #Sentences to display next output image if session was already
                #segmented
                self.showOutputImageFromSession()
                if self.temperaturesWereAcquired:
                    self.messagePrint("La temperatura media es: " + str(self.meanTemperatures[self.imageIndex]))
                    self.ui_window.temperatureLabel.setText(str(np.round(self.meanTemperatures[self.imageIndex], 3)))

    def saveImage(self):
        #Saves segmented image
        pass

    def getModelsPath(self):
        self.modelDialog=QFileDialog(self)
        self.modelDialog.setDirectory(QDir.currentPath())        
        self.modelDialog.setFileMode(QFileDialog.FileMode.Directory)
        self.modelsPath = self.modelDialog.getExistingDirectory()
        if self.modelsPath:
            self.modelsPathExists = True
            self.modelList = []
            for root, dirs, files in os.walk(self.modelsPath):
                for file in files:
                    self.modelList.append(os.path.join(root,file))
            self.modelQuantity = len(self.modelList)
            self.modelIndex = 0
            self.models = files
            self.ui_window.modelComboBox.addItems(self.models)


    def feetSegment(self):
        self.messagePrint("Segmentando imagen...")
        self.i2s.setModel(self.model)
        self.i2s.setPath(self.opdir)
        self.i2s.extract()
        self.showSegmentedImage()
        self.isSegmented = True
        self.messagePrint("Imagen segmentada exitosamente")

    def sessionSegment(self):
        self.messagePrint("Segmentando toda la sesion...")
        self.sessionIsSegmented = False
        self.s2s.setModel(self.model)
        self.s2s.setPath(self.defaultDirectory)
        self.s2s.whole_extract(self.fileList)
        self.produceSegmentedSessionOutput()
        self.showOutputImageFromSession()
        self.messagePrint("Se ha segmentado exitosamente la sesion con "+ self.i2s.model)
        self.sessionIsSegmented = True

    def showSegmentedImage(self):
        #Applies segmented zone to input image, showing only feet
        threshold =  0.5
        img = plt.imread(self.opdir)/255
        Y = self.i2s.Y_pred
        Y = Y / Y.max()
        Y = np.where( Y >= threshold  , 1 , 0)
        self.Y =remove_small_objects( Y[0])     #Eventually required by temp_extract
        Y = cv2.resize(Y[0], (img.shape[1],img.shape[0]), interpolation = cv2.INTER_NEAREST) # Resize the prediction to have the same dimensions as the input 
        if self.ui_window.rainbowCheckBoxImport.isChecked():
            cmap = 'rainbow'
        else:
            cmap = 'gray'
        plt.figure()
        plt.plot(Y*img[:,:,0])
        plt.savefig("outputs/output.jpg")
        #plt.imsave("outputs/output.jpg" , Y*img[:,:,0] , cmap=cmap)
        self.ui_window.outputImgImport.setPixmap("outputs/output.jpg")
    
    def produceSegmentedSessionOutput(self):
        #Recursively applies showSegmentedImage to whole session
        self.Y=[]
        for i in range(len(self.outfiles)):
            threshold =  0.5
            img = plt.imread(self.fileList[i])/255
            Y = self.s2s.Y_pred[i]
            Y = Y / Y.max()
            Y = np.where( Y >= threshold  , 1 , 0)

            self.Y.append(remove_small_objects(Y[0]))     #Eventually required by temp_extract
            print(self.Y[0].shape)
            Y = cv2.resize(Y[0], (img.shape[1],img.shape[0]), interpolation = cv2.INTER_NEAREST) # Resize the prediction to have the same dimensions as the input 
            if self.ui_window.rainbowCheckBox.isChecked():
                cmap = 'rainbow'
            else:
                cmap = 'gray'
            # plt.figure()
            # plt.imshow(Y*img[:,:,0])
            # plt.axis('off')
            # plt.savefig(self.outfiles[i])
            plt.imsave(self.outfiles[i], Y*img[:,:,0] , cmap=cmap)


    def showOutputImageFromSession(self):
        self.ui_window.outputImgImport.setPixmap(self.outfiles[self.imageIndex])

    def segment(self):
        if self.ui_window.sessionCheckBox.isChecked():

            if self.defaultDirectoryExists and self.i2s.model!=None and self.s2s.model!=None:
                self.sessionSegment()
            else:
                self.messagePrint("Error. Por favor verifique que se ha cargado el modelo y la sesion de entrada.")
        else:
            if self.inputExists and self.modelsPathExists and self.model!=None:
                self.feetSegment()
            else:
                self.messagePrint("No se ha seleccionado sesion de entrada")

    def manual_segment(self):
        print("Se abrirá diálogo de extracción manual")
        self.manual=manualSeg()
        self.manual.show()
        return

    def temp_extract(self):
        if (self.inputExists and (self.isSegmented or self.sessionIsSegmented)):
            if self.ui_window.autoScaleCheckBoxImport.isChecked and self.ui_window.sessionCheckBox.isChecked():
                #Get automatic scales
                scale_range = self.extract_multiple_scales(self.s2s.img_array)
                print(scale_range)
                self.ui_window.minSpinBoxImport.setValue(scale_range[self.imageIndex][0])
                self.ui_window.maxSpinBoxImport.setValue(scale_range[self.imageIndex][1])
            elif not self.ui_window.autoScaleCheckBoxImport.isChecked():
                scale_range = [self.ui_window.minSpinBoxImport.value() , self.ui_window.maxSpinBoxImport.value()] 

            if self.ui_window.sessionCheckBox.isChecked():   #If segmentation was for full session
                self.meanTemperatures = []   #Whole feet mean temperature for all images in session
                if self.ui_window.autoScaleCheckBoxImport.isChecked():
                    for i in range(len(self.outfiles)):
                        self.meanTemperatures.append(mean_temperature(self.s2s.Xarray[i,:,:,0] , self.Y[i][:,:,0] , scale_range[i], plot = False))
                else:
                    for i in range(len(self.outfiles)):
                        self.meanTemperatures.append(mean_temperature(self.s2s.Xarray[i,:,:,0] , self.Y[i][:,:,0] , scale_range, plot = False))
                self.messagePrint("La temperatura media es: " + str(self.meanTemperatures[self.imageIndex]))
                self.temperaturesWereAcquired = True
            else:      #If segmentation was for single image
                scale_range = self.extract_scales(self.i2s.Xarray)
                mean = mean_temperature(self.i2s.Xarray[:,:,0] , self.Y[:,:,0] , scale_range, plot = False)
                self.messagePrint("La temperatura media es: " + str(mean))

            if (self.ui_window.plotCheckBox.isChecked()):  #If user asked for plot
                self.messagePrint("Se generara plot de temperatura...")
                self.getTimes()
                print(self.timeList)
                self.tempPlot()

        elif self.inputExists:
            self.messagePrint("No se ha segmentado previamente la imagen ")
        else:
            self.messagePrint("No se han seleccionado imagenes de entrada")

    def toggleModel(self):
        self.modelIndex = self.ui_window.modelComboBox.currentIndex()
        self.messagePrint("Cargando modelo: " + self.models[self.modelIndex]
                        +" Esto puede tomar unos momentos...")
        try:
            self.model = self.modelList[self.modelIndex]
            self.s2s.setModel(self.model)
            self.i2s.setModel(self.model)
            self.s2s.loadModel()
            self.i2s.loadModel()
            self.ui_window.loadedModelLabel.setText(self.model)
            self.messagePrint("Modelo " + self.models[self.modelIndex] + " cargado exitosamente")
        except:
            self.messagePrint("Error al cargar el modelo "+ self.models[self.modelIndex])

    def tempPlot(self):
        plt.figure()
        x = np.linspace(min(self.timeList), max(self.timeList), 200)
        spl = make_interp_spline(self.timeList, self.meanTemperatures, k=3)
        y = spl(x) 
        plt.plot(x , y, '-.', color='salmon')
        plt.plot(self.timeList , self.meanTemperatures , '-o', color='slategrey')
        plt.title("Temperatura media de pies")
        plt.xlabel("Tiempo (min)")
        plt.ylabel("Temperatura (°C)")
        plt.grid()
        plt.show()
        self.messagePrint("Plot de temperatura generado exitosamente")
        #Produce plot 

    def openImage(self):
        self.fileDialog=QFileDialog(self)
        if self.defaultDirectoryExists:
            self.fileDialog.setDirectory(self.defaultDirectory)
        else:
            self.fileDialog.setDirectory(QDir.currentPath())        
        filters =  ["*.png", "*.xpm", "*.jpg"]
        self.fileDialog.setNameFilters("Images (*.png *.jpg)")
        self.fileDialog.selectNameFilter("Images (*.png *.jpg)")
        #self.fileDialog.setFilter(self.fileDialog.selectedNameFilter())
        self.opdir = self.fileDialog.getOpenFileName()[0]
        self.imagesDir = os.path.dirname(self.opdir) 
        if self.opdir:
            self.inputExists = True
            self.ui_window.inputImgImport.setPixmap(self.opdir)

    def openFolder(self):
        self.folderDialog=QFileDialog(self)
        self.folderDialog.setDirectory(QDir.currentPath())        
        self.folderDialog.setFileMode(QFileDialog.FileMode.Directory)
        self.defaultDirectory = self.folderDialog.getExistingDirectory()
        self.imagesDir = self.defaultDirectory
        if self.defaultDirectory:
            self.defaultDirectoryExists = True
            first_image = str(self.defaultDirectory + "/t0.jpg")
            print(first_image)
            self.ui_window.inputImgImport.setPixmap(first_image)
            self.opdir = first_image
            self.inputExists = True
            self.findImages()
            self.sessionIsSegmented = False

    def toggleFullscreen(self):
        if self.fullScreen:
            self.ui_window.showNormal()
            self.fullScreen = False
        else:
            self.ui_window.showFullScreen()
            self.fullScreen = True

    def exit_(self):
        sys.exit(app.exec_())

    def howToUse(self):
        os.system("xdg-open README.html")

    def exportReport(self):
        self.messagePrint("Generando reporte...") 
        """
        GENERATE A PDF REPORT FOR THE PATIENT
        INPUT: SELF, PATIENT DIR
        RETURN: NONE
        ACTION: COMPILE PDF TEXT BASED ON
        """
        self.messagePrint("Desarrollo no implementado, disponible en futuras versiones.")
        raise NotImplementedError()

    def animate(self):      
        """
        Produces gif animation based on mean temperatures for whole session
        Initially, all feet has same color, for section segmentation has been not implemented yet
        """
        self.messagePrint("Iniciando animacion...")
        self.messagePrint("Desarrollo no implementado, disponible en futuras versiones.")
        raise NotImplementedError()

    def updateSoftware(self):
        try:
            os.system("git pull")
            self.messagePrint("Se ha actualizado exitosamente la interfaz. Se sugiere reiniciar interfaz")
        except:
            self.messagePrint("Error al actualizar")
            raise RemotePullException()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    window.ui_window.show()  
    sys.exit(app.exec_())
