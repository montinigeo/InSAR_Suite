<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.0" minScale="1e+08" maxScale="0" hasScaleBasedVisibilityFlag="0" styleCategories="AllStyleCategories">
  <pipe>
    <rasterrenderer opacity="1" alphaBand="-1" type="singlebandpseudocolor" band="1" classificationMin="0" classificationMax="100">
      <rasterTransparency/>
      <minMaxOrigin>
        <limits>MinMax</limits>
        <extent>WholeRaster</extent>
        <statAccuracy>Estimated</statAccuracy>
        <cumulativeCutLower>0.02</cumulativeCutLower>
        <cumulativeCutUpper>0.98</cumulativeCutUpper>
        <stdDevFactor>2</stdDevFactor>
      </minMaxOrigin>
      <rastershader>
        <colorrampshader colorRampType="DISCRETE" clip="0" classificationMode="1" labelPrecision="0">
          <colorramp name="[source]" type="gradient">
            <Option type="Map">
              <Option name="color1" value="255,0,0,255" type="QString"/>
              <Option name="color2" value="0,100,0,255" type="QString"/>
            </Option>
          </colorramp>
          <item alpha="255" color="#ff0000" value="25" label="0 - 25%"/>
          <item alpha="255" color="#ff8000" value="50" label="25 - 50%"/>
          <item alpha="255" color="#90ee90" value="75" label="50 - 75%"/>
          <item alpha="255" color="#006400" value="100" label="75 - 100%"/>
        </colorrampshader>
      </rastershader>
    </rasterrenderer>
    <brightnesscontrast brightness="0" contrast="0" gamma="1"/>
    <huesaturation colorizeStrength="100" colorizeOn="0" colorizeBlue="128" colorizeGreen="128" colorizeRed="255" grayscaleMode="0" invertColors="0" saturation="0"/>
    <rasterresampler maxOversampling="2"/>
    <resamplingStage>resamplingFilter</resamplingStage>
  </pipe>
</qgis>
