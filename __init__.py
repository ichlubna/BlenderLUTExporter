import ast
import array
import bpy
import mathutils
import os
import re
import tempfile
import time
from bpy_extras.io_utils import ExportHelper
from bpy_extras.io_utils import ImportHelper

bl_info = {
    "name": "LUT Exporter & Importer",
    "description": "Converts the adjustment layer effects into LUT .cube file and back",
    "author": "ichlubna",
    "version": (2, 0),
    "blender": (5, 0, 0),
    "location": "VSE",
    "warning": "",
    "tracker_url": "https://github.com/ichlubna/BlenderLUTExporter",
    "support": "COMMUNITY",
    "category": "Import-Export"
}

def lutInputVector(index, size):
    r = (index % size) / (size-1.0)
    g = ((index // size) % size) / (size-1.0)
    b = ((index // size**2) % size) / (size-1.0)   
    return mathutils.Color((r, g, b))

def sequenceEditorOverrides(context):
    for window in context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type == 'SEQUENCE_EDITOR':
                region = next(
                    (r for r in area.regions if r.type == 'WINDOW'),
                    None
                )
                if region:
                    yield {
                        "window": window,
                        "screen": screen,
                        "area": area,
                        "region": region
                    }
                    
def grid(x, y, node):
    xOffset = 200
    yOffset = 200
    node.location = (x*xOffset, y*yOffset)

def newNode(x, y, nodeGroup, type):
    node = nodeGroup.nodes.new(type = type)
    grid(x, y, node)
    return node

def connect(firstNode, secondNode, firstOutput, secondInput, nodeGroup):
        nodeGroup.links.new(firstNode.outputs[firstOutput], secondNode.inputs[secondInput])

def toFront(enumItems, targetName):
    index = next((i for i, item in enumerate(enumItems) if item[1] == targetName), None)
    if index is not None:
        item = enumItems.pop(index)
        enumItems.insert(0, item)
    return enumItems

def exceptionList(e):
    match = re.search(r"\((.*)\)", str(e))
    if match:
        tuple_str = "(" + match.group(1) + ")"
        t = ast.literal_eval(tuple_str)
        data = list(t)
    else:
        data = []
    return [(value, value, "") for value in data]

def listDisplays(self, context):
    displays = []
    try:
        context.scene.display_settings.display_device = "DefinitelyNotExistingDevice"
    except Exception as e:
        displays = exceptionList(e)
    toFront(displays, context.scene.display_settings.display_device)
    return displays

def listViews(self, context):
    views = []
    try:
        context.scene.view_settings.view_transform = "DefinitelyNotExistingView"
    except Exception as e:
        views = exceptionList(e)
    toFront(views, context.scene.display_settings.display_device)
    return views

def sceneLinear():
    filePath = bpy.utils.resource_path("SYSTEM")
    filePath = os.path.join(filePath, "datafiles", "colormanagement", "config.ocio")
    profile = "Linear Rec.709"
    if os.path.isfile(filePath):
        with open(filePath, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("scene_linear:"):
                    profile = line.split(":", 1)[1].strip()
                    break
    return profile.strip()

def listColorSpaces(self, context):
    rna = bpy.types.ColorManagedInputColorspaceSettings.bl_rna
    enum_prop = rna.properties['name']
    colorSpaces = [item.identifier for item in enum_prop.enum_items]
    colorSpaces = [(value, value, "") for value in colorSpaces]
    return toFront(colorSpaces, sceneLinear()) 

class LUT_OT_Export(bpy.types.Operator, ExportHelper):
    """ Exports the LUT """
    bl_idname = "sequencer.lut_export"
    bl_label = "Export LUT"
    
    filename_ext: bpy.props.StringProperty(name="LUT file extension", default=".cube")
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    LUTresolution: bpy.props.IntProperty(name="LUT resolution", description="The amount of samples - dimension of the LUT cube (higher increases the quality but results in higher file size and slower export)", default=33, min=0)

    def getSamples(self, context):
        context.sequencer_scene.sequence_editor.active_strip.select = True
        bpy.ops.sequencer.copy()
        originalScene = bpy.context.sequencer_scene
        
        start = 0
        end = self.LUTresolution**3
        
        bpy.ops.scene.new_sequencer_scene() 
        context.scene.name = "LUTSamplingScene"  
        context.scene.frame_start = start
        context.scene.frame_end = end
        
        colorStrip = None
        adjustmentStrip = None
        for override in sequenceEditorOverrides(context):
            with context.temp_override(**override):
                bpy.ops.sequencer.paste()
                adjustmentStrip = context.scene.sequence_editor.active_strip
                bpy.ops.sequencer.effect_strip_add(type = "COLOR", move_strips=False, frame_start = start, length = end-start, channel=1, replace_sel = True)
                colorStrip = context.scene.sequence_editor.active_strip
                break
    
        adjustmentStrip.channel = 2
        adjustmentStrip.frame_final_start = start
        adjustmentStrip.frame_final_end = end        
             
        temp = tempfile.TemporaryDirectory()
        fileName = "LUTsample.exr"
        
        renderInfo = context.scene.render
        renderInfo.resolution_x = 4
        renderInfo.resolution_y = 4
        renderInfo.image_settings.file_format = "OPEN_EXR"
        renderInfo.image_settings.color_depth = "32"
        file = os.path.join(temp.name, fileName)
        renderInfo.filepath = file
        renderInfo.use_sequencer = True
        context.scene.display_settings.display_device = "sRGB"
        context.scene.view_settings.view_transform = "Raw"

        image = bpy.data.images.new(fileName, 0, 0)
        image.use_half_precision = False
        image.colorspace_settings.name = "scene_linear"
        image.colorspace_settings.is_data = True
        samples = []

        context.window_manager.progress_begin(min=start, max=end)
        for frame in range(start, end):
            bpy.context.window_manager.progress_update(value=frame)
            colorStrip.color = lutInputVector(frame, self.LUTresolution) 
            # When using write_still instead of the save(), the colors are always corrected and not right
            bpy.ops.render.render(write_still=False)
            bpy.data.images["Render Result"].save_render(file)
            image.source = 'FILE'
            image.filepath = file
            image.reload()
            image.update()  
            samples.append(image.pixels[0:3])
        
        bpy.context.window_manager.progress_end()
        bpy.ops.scene.delete()
        context.workspace.sequencer_scene = originalScene
        temp.cleanup() 
        return samples   

    @classmethod
    def poll(cls, context):
        activeStrip = context.sequencer_scene.sequence_editor.active_strip
        if activeStrip == None: 
            return False
        if activeStrip.type != "ADJUSTMENT":  
            bpy.types.Operator.poll_message_set("Adjustment layer in VSE needs to be selected.")
            return False
        return True

    def execute(self, context):    
        file = open(self.filepath, "w")
        file.write('TITLE "Generated by Blender LUT Exporter & Importer"\n')
        file.write('# https://github.com/ichlubna/BlenderLUTExporter\n')
        file.write('# Expected input color profile: ' + sceneLinear() + '\n')
        file.write('LUT_3D_SIZE ' + str(self.LUTresolution) + '\n')
        samples = self.getSamples(context)
        for sample in samples:
            file.write(str(sample[0]) + " " + str(sample[1]) + " " + str(sample[2]) + "\n")            
        file.close()
        return {"FINISHED"}
    
class LUT_OT_Import(bpy.types.Operator, ImportHelper):
    """ Imports the LUT """
    bl_idname = "sequencer.lut_import"
    bl_label = "Import LUT"
    
    filename_ext: bpy.props.StringProperty(name="LUT file extension", default=".cube", subtype='FILE_PATH')
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    baseSpace: bpy.props.EnumProperty(name="Reference Color Space", items=listColorSpaces, default=0)

    @classmethod
    def poll(cls, context):
        if context.sequencer_scene == None: 
            bpy.types.Operator.poll_message_set("No active sequencer scene found.")
            return False
        return True
    
    def loadFile(self, path):
        size = None
        title = None
        data = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.upper().startswith("TITLE"):
                    if '"' in line:
                        title = line.split('"', 1)[1].rsplit('"', 1)[0]
                    continue
                if line.upper().startswith("LUT_3D_SIZE"):
                    parts = line.split()
                    size = int(parts[1])
                    continue
                parts = line.split()
                if len(parts) == 3:
                    try:
                        r, g, b = map(float, parts)
                        data.append((r, g, b))
                    except ValueError:
                        pass 

        return {"title": title, "size": size, "data": data}

    def LUTTexture(self, context, size, data, name):
        texture = bpy.data.images.new(name=name, width=size*size, height=size)
        texture.use_half_precision = False
        texture.colorspace_settings.name = "scene_linear"
        texture.colorspace_settings.is_data = True
        length = size**3
        pixels = array.array('f', [1.0] * (length*4))
        step = 1.0/(size-1)
        for index in range(0, length):
            inputColor = lutInputVector(index, size)
            coords = [0, 0, 0]
            for channel in range(0, 3):
                coords[channel] = round(inputColor[channel]*(size-1))
            # The texture layout is B blocks of RxG resolution
            # Might be better to repeat pixels to avoid crosstalk in bilinear or bicubic interpolation between the blocks
            linearCoords = (size*size*coords[2] + coords[1]*size + coords[0]) * 4
            color = data[index]
            for channel in range(0, 3):
                pixels[linearCoords + channel] = color[channel]
            pixels[linearCoords + 3] = 1.0
        texture.pixels.foreach_set(pixels)
        texture.update()
        return texture
        
    def LUTcompositorGroup(self, context, texture, name, size):
        bpy.ops.node.new_compositor_sequencer_node_group(name=name)
        nodeGroup = bpy.data.node_groups.get(name)
        
        inputNode = None
        outputNode = None
        for node in nodeGroup.nodes:
            if node.type == 'GROUP_INPUT':
                inputNode = node
            elif node.type == 'GROUP_OUTPUT':
                outputNode = node
            elif node.type == 'VIEWER':
                nodeGroup.nodes.remove(node)
        
        grid(0, 3, inputNode)
        LUTSizeNode = newNode(0, 5, nodeGroup, 'ShaderNodeValue')
        LUTSizeNode.outputs[0].default_value = size
        indexSubtractNode = newNode(0, 4, nodeGroup, 'ShaderNodeMath')
        indexSubtractNode.inputs[1].default_value = 1
        indexSubtractNode.operation = "SUBTRACT"
        multiplyCoordsNode = newNode(0, 2, nodeGroup, 'ShaderNodeVectorMath')
        multiplyCoordsNode.operation = "MULTIPLY" 
        LUTImageNode = newNode(0, 1, nodeGroup, 'CompositorNodeImage')
        LUTImageNode.image = texture
        LUTImageInfoNode = newNode(0, 0, nodeGroup, 'CompositorNodeImageInfo')
        halfPixelNode = newNode(1, 0, nodeGroup, 'ShaderNodeVectorMath')
        halfPixelNode.operation = "DIVIDE" 
        halfPixelNode.inputs[0].default_value = (0.5, 0.5, 0.5)
        separateNode = newNode(1, 2, nodeGroup, 'ShaderNodeSeparateXYZ')
        powerNode = newNode(1, 5, nodeGroup, 'ShaderNodeMath')
        powerNode.operation = "POWER" 
        powerNode.inputs[1].default_value = 2
        LUTSizeVectorNode = newNode(1, 4, nodeGroup, 'ShaderNodeCombineXYZ')
        LUTSizeVectorNode.inputs[2].default_value = 1.0
        floorNode = newNode(2, 5, nodeGroup, 'ShaderNodeMath')
        floorNode.operation = "FLOOR"
        ceilNode = newNode(2, 1, nodeGroup, 'ShaderNodeMath')
        ceilNode.operation = "CEIL"
        ceilClampNode = newNode(2, 0, nodeGroup, 'ShaderNodeClamp')
        ceilClampNode.clamp_type = "MINMAX"
        floorMultiplyNode = newNode(3, 5, nodeGroup, 'ShaderNodeMath')
        floorMultiplyNode.operation = "MULTIPLY" 
        ceilMultiplyNode = newNode(3, 1, nodeGroup, 'ShaderNodeMath')
        ceilMultiplyNode.operation = "MULTIPLY" 
        floorAddNode = newNode(4, 5, nodeGroup, 'ShaderNodeMath')
        floorAddNode.operation = "ADD" 
        floorCombineNode = newNode(4, 4, nodeGroup, 'ShaderNodeCombineXYZ')
        floorCombineNode.inputs[2].default_value = 1
        floorNormalizeNode = newNode(4, 3, nodeGroup, 'ShaderNodeVectorMath')
        floorNormalizeNode.operation = "DIVIDE" 
        floorHalfPixelNode = newNode(4, 2, nodeGroup, 'ShaderNodeVectorMath')
        floorHalfPixelNode.operation = "ADD" 
        ceilAddNode = newNode(4, 1, nodeGroup, 'ShaderNodeMath')
        ceilAddNode.operation = "ADD" 
        ceilCombineNode = newNode(4, 0, nodeGroup, 'ShaderNodeCombineXYZ')
        ceilCombineNode.inputs[2].default_value = 1
        ceilNormalizeNode = newNode(4, -1, nodeGroup, 'ShaderNodeVectorMath')
        ceilNormalizeNode.operation = "DIVIDE" 
        ceilHalfPixelNode = newNode(4, -2, nodeGroup, 'ShaderNodeVectorMath')
        ceilHalfPixelNode.operation = "ADD" 
        floorPickerNode = newNode(5, 2, nodeGroup, 'CompositorNodeMapUV')
        floorPickerNode.inputs[2].default_value = "Bilinear"
        floorPickerNode.inputs[3].default_value = "Extend"
        floorPickerNode.inputs[4].default_value = "Extend"
        ceilPickerNode = newNode(5, -2, nodeGroup, 'CompositorNodeMapUV')
        ceilPickerNode.inputs[2].default_value = "Bilinear"
        ceilPickerNode.inputs[3].default_value = "Extend"
        ceilPickerNode.inputs[4].default_value = "Extend"
        mixFactorSubtractNode = newNode(6, 2, nodeGroup, 'ShaderNodeMath')
        mixFactorSubtractNode.operation = "SUBTRACT"
        mixFactorDivideNode = newNode(6, 1, nodeGroup, 'ShaderNodeMath')
        mixFactorDivideNode.operation = "DIVIDE"
        mixNode = newNode(6, 0, nodeGroup, 'ShaderNodeMix')
        mixNode.clamp_factor = True
        mixNode.data_type = "RGBA"
        convertSpaceNode = newNode(1, 2, nodeGroup, 'CompositorNodeConvertColorSpace')
        convertSpaceNode.to_color_space = self.baseSpace
        grid(7, 0, outputNode)

        nodeGroup.links.clear()
        connect(LUTSizeNode, indexSubtractNode, 0, 0, nodeGroup)
        connect(inputNode, convertSpaceNode, 0, 0, nodeGroup)
        connect(convertSpaceNode, multiplyCoordsNode, 0, 0, nodeGroup)
        connect(indexSubtractNode, multiplyCoordsNode, 0, 1, nodeGroup)
        connect(LUTImageNode, LUTImageInfoNode, 0, 0, nodeGroup)
        connect(LUTImageInfoNode, halfPixelNode, 1, 1, nodeGroup)
        connect(multiplyCoordsNode, separateNode, 0, 0, nodeGroup)
        connect(LUTSizeNode, powerNode, 0, 0, nodeGroup)
        connect(powerNode, LUTSizeVectorNode, 0, 0, nodeGroup)
        connect(LUTSizeNode, LUTSizeVectorNode, 0, 1, nodeGroup)
        connect(separateNode, floorNode, 1, 0, nodeGroup)
        connect(separateNode, ceilNode, 1, 0, nodeGroup)
        connect(ceilNode, ceilClampNode, 0, 0, nodeGroup)
        connect(indexSubtractNode, ceilClampNode, 0, 2, nodeGroup)
        connect(floorNode, floorMultiplyNode, 0, 1, nodeGroup)
        connect(LUTSizeNode, floorMultiplyNode, 0, 0, nodeGroup)
        connect(ceilClampNode, ceilMultiplyNode, 0, 1, nodeGroup)
        connect(LUTSizeNode, ceilMultiplyNode, 0, 0, nodeGroup)
        connect(floorMultiplyNode, floorAddNode, 0, 0, nodeGroup)
        connect(separateNode, floorAddNode, 0, 1, nodeGroup)
        connect(floorAddNode, floorCombineNode, 0, 0, nodeGroup)
        connect(separateNode, floorCombineNode, 2, 1, nodeGroup)
        connect(floorCombineNode, floorNormalizeNode, 0, 0, nodeGroup)
        connect(LUTSizeVectorNode, floorNormalizeNode, 0, 1, nodeGroup)
        connect(floorNormalizeNode, floorHalfPixelNode, 0, 0, nodeGroup)
        connect(halfPixelNode, floorHalfPixelNode, 0, 1, nodeGroup)
        connect(ceilMultiplyNode, ceilAddNode, 0, 0, nodeGroup)
        connect(separateNode, ceilAddNode, 0, 1, nodeGroup)
        connect(ceilAddNode, ceilCombineNode, 0, 0, nodeGroup)
        connect(separateNode, ceilCombineNode, 2, 1, nodeGroup)
        connect(ceilCombineNode, ceilNormalizeNode, 0, 0, nodeGroup)
        connect(LUTSizeVectorNode, ceilNormalizeNode, 0, 1, nodeGroup)
        connect(ceilNormalizeNode, ceilHalfPixelNode, 0, 0, nodeGroup)
        connect(halfPixelNode, ceilHalfPixelNode, 0, 1, nodeGroup)
        connect(LUTImageNode, floorPickerNode, 0, 0, nodeGroup)
        connect(floorHalfPixelNode, floorPickerNode, 0, 1, nodeGroup)
        connect(LUTImageNode, ceilPickerNode, 0, 0, nodeGroup)
        connect(ceilHalfPixelNode, ceilPickerNode, 0, 1, nodeGroup)
        connect(separateNode, mixFactorSubtractNode, 1, 0, nodeGroup)
        connect(floorNode, mixFactorSubtractNode, 0, 1, nodeGroup)
        connect(mixFactorSubtractNode, mixFactorDivideNode, 0, 0, nodeGroup)
        connect(mixFactorDivideNode, mixNode, 0, 0, nodeGroup)
        connect(floorPickerNode, mixNode, 0, 6, nodeGroup)
        connect(ceilPickerNode, mixNode, 0, 7, nodeGroup)
        connect(mixNode, outputNode, 2, 0, nodeGroup)        
        return nodeGroup

    def execute(self, context):
        file = self.loadFile(self.filepath)
        length = file["size"]**3
        modifier = None
        uniqueName = str(int(time.time() * 1000))
        
        for override in sequenceEditorOverrides(context):
            with context.temp_override(**override):
                bpy.ops.sequencer.effect_strip_add(type = "ADJUSTMENT", move_strips=False, frame_start = 0, length = 100, channel=1, replace_sel = True)
                adjustmentStrip = context.scene.sequence_editor.active_strip
                if adjustmentStrip == None:
                    adjustmentStrip = context.sequencer_scene.sequence_editor.strips[-1]
                adjustmentStrip.name = "LUT"
                bpy.ops.sequencer.strip_modifier_add(type = "COMPOSITOR")
                modifier = adjustmentStrip.modifiers[-1]
                context.scene.render.compositor_device = "GPU"
                context.scene.render.compositor_precision = "FULL"
                break

        texture = self.LUTTexture(context, file["size"], file["data"], uniqueName)
        modifier.node_group = self.LUTcompositorGroup(context, texture, uniqueName, file["size"])
        return {"FINISHED"}

def drawExport(self, context):
    self.layout.operator(LUT_OT_Export.bl_idname, text="Adjustment Layer as LUT (.cube)")
    
def drawImport(self, context):
    self.layout.operator(LUT_OT_Import.bl_idname, text="LUT as Adjustment Layer (.cube)")

def register():
    if bpy.app.background:
        return 
    bpy.utils.register_class(LUT_OT_Export)
    bpy.utils.register_class(LUT_OT_Import)
    bpy.types.TOPBAR_MT_file_export.append(drawExport)
    bpy.types.TOPBAR_MT_file_import.append(drawImport)
    
def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(drawExport)
    bpy.types.TOPBAR_MT_file_import.remove(drawImport)
    bpy.utils.unregister_class(LUT_OT_Export)
    bpy.utils.unregister_class(LUT_OT_Import)
    
if __name__ == "__main__" :
    register()        
