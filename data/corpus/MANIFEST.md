# Corpus demo — fuentes descargadas (2026-11-06)
Los PDFs viven en `raw/` (ignorado por git); `SHA256SUMS` fija los bytes ingeridos.

| Archivo | Fuente | Jurisdicción |
|---|---|---|
| LFT.pdf | diputados.gob.mx/LeyesBiblio/pdf/LFT.pdf | MX federal |
| CPEUM.pdf | diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf | MX federal |
| CCF.pdf, CPF.pdf | diputados.gob.mx/LeyesBiblio/pdf/{CCF,CPF}.pdf | MX federal |
| CC_NL.pdf | hcnl.gob.mx (Código Civil para el Estado de Nuevo León) | MX-NL |
| REG_JUSTICIA_CIVICA_MTY.pdf | monterrey.gob.mx/pdf/reglamentos/1/Reglamento_de_Justicia_Civica_Municipal_de_Monterrey_Nuevo_Leon.pdf (39 pp.; sustituye al Reglamento de Policía y Buen Gobierno, abrogado según dictamen de cabildo 2026) | MX-NL / Monterrey |
| CA_LABOR_CODE.txt | Extraído de `downloads.leginfo.legislature.ca.gov/pubinfo_2025.zip` (volcado oficial, 2026-11-01): `LAW_SECTION_TBL` con `LAW_CODE=LAB`, `ACTIVE_FLG=Y`, 2 636 secciones en orden natural (versión vigente por fecha; ver scripts/extract_ca_code.py), con cabeceras Division/Title/Part/Chapter/Article | US-CA |
| REG_ADMIN_MTY.pdf | monterrey.gob.mx/pdf/reglamentos/1/ (Reglamento de la Administración Pública Municipal) | MX-NL / Monterrey |

Nota: leginfo.legislature.ca.gov y Justia devuelven 403 a descargas automáticas; no se evadió el bloqueo, se usó el volcado oficial. El .zip (1.2 GB) se borró tras extraer.
