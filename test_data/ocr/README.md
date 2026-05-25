Small OCR fixtures are generated in tests from embedded text. The generated
PNG stores `marvis_ocr_text` metadata, and the generated PDF stores
`/MarvisOCRText` on its embedded image XObject so tests stay offline and do not
depend on a system OCR engine.
