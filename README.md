# Kiln Tile Bench

Inspect fired ceramic tiles from registered face, macro, or grazing-light photographs. The demonstration uses a synthetic indigo-border tile with deliberate pits, reflectance witnesses, missing camera support, and registration marks.

Run `./start.sh` and open the local address it prints. The default port is 19991. Compose stores all runs and archived evidence in the named tile volume. Run `./start.sh --down` to stop the app and keep its data.

Upload JPEG, PNG, or TIFF photographs and click the tile's four known corners clockwise from upper left. Use paired photographs of the same measured extent for shared observation or color comparisons. Mark a patch, a two-point rim transect, or ordered decal landmarks, then choose **Take reading**. A saved reading retains each original photograph, its calibration, the marks, measured values, and the annotated overlay. Download a ZIP or CSV from a saved reading. The Image preparation room provides independent crop, color, and image comparison tools.

Measurements include glaze tone, glaze and decal contrast, decal likeness area, brush spacing, compact dark pinholes, the glaze rim transition, decal offsets, grazing response against marked witnesses, paired visibility, raking spots, and fired color difference. Use a closely controlled photograph for each comparison. Pinhole detection separates compact dark candidates from broad marks using a luminance threshold and physical size. Grazing response measures the difference between two photographs against their own matte and gloss witnesses. These are image measurements, with no calibration to physical gloss units or ceramic material identification.

A run accepts at most 12 photographs, 100 marks, and 160 readings. Photograph input is limited to 20 MiB and 14 megapixels. Calibrated output uses up to four pixels per millimetre and a long edge at most 1,300 pixels. Transparent and fully clipped positions are left unmeasured.
