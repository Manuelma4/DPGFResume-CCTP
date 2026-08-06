from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import fitz
from docx import Document
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import store
from app.excel_export import export_analysis, export_analysis_files
from app.extractors import extract_document
from app.main import app
from app.parser import parse_document, recompute_stats


def make_docx(path: Path) -> None:
    document = Document()
    document.add_heading("LOT 03 — GROS ŒUVRE", level=1)
    document.add_heading("3.1 INFRASTRUCTURES", level=2)
    document.add_heading("3.1.1 Terrassements", level=3)
    document.add_paragraph(
        "Terrassements en pleine masse, évacuation comprise. Unité de règlement : m³."
    )
    document.add_heading("3.1.2 Béton de propreté", level=3)
    document.add_paragraph("Béton dosé suivant prescriptions, quantité 12,5 m².")
    document.add_heading("3.2 OUVRAGES DIVERS", level=2)
    document.add_heading("3.2.1 Réservations et percements", level=3)
    document.add_paragraph("Ensemble des sujétions, au forfait.")
    document.add_heading("3.2.9 Location mensuelle", level=3)
    document.add_paragraph("Installation maintenue pendant le chantier. Unité : mois.")
    document.save(path)


def sample_analysis(lot: dict) -> dict:
    return {
        "id": "a" * 32,
        "project": {
            "name": "BONDUELLE — Bâtiment Gay Lussac",
            "reference": "25_189",
            "client": "BONDUELLE",
            "phase": "PRO",
            "due_date": "",
        },
        "lots": [lot],
        "stats": recompute_stats([lot]),
    }


class ExtractionTests(unittest.TestCase):
    def test_docx_hierarchy_units_and_traceability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CCTP LOT 03.docx"
            make_docx(path)
            extracted = extract_document(path)
            lot = parse_document(extracted, "src_test")

        self.assertEqual(lot["code"], "03")
        self.assertIn("GROS", lot["title"].upper())
        items = [line for line in lot["lines"] if line["kind"] == "item"]
        self.assertGreaterEqual(len(items), 3)
        by_code = {line["code"]: line for line in items}
        self.assertEqual(by_code["3.1.1"]["unit"], "m³")
        self.assertEqual(by_code["3.1.2"]["quantity"], 12.5)
        self.assertEqual(by_code["3.2.9"]["unit"], "mois")
        self.assertIn("source_excerpt", by_code["3.2.1"])

    def test_pdf_page_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "LOT 08 ELECTRICITE.pdf"
            pdf = fitz.open()
            page = pdf.new_page()
            page.insert_text((50, 70), "LOT 08 - ELECTRICITE", fontsize=16)
            page.insert_text((50, 110), "8.1 COURANTS FORTS", fontsize=14)
            page.insert_text((50, 140), "8.1.1 Tableau divisionnaire", fontsize=12)
            page.insert_text((50, 165), "Fourniture et pose de l'ensemble au forfait.", fontsize=10)
            pdf.save(path)
            pdf.close()
            lot = parse_document(extract_document(path), "src_pdf")

        item = next(line for line in lot["lines"] if line["code"] == "8.1.1")
        self.assertEqual(item["source_page"], 1)
        self.assertEqual(item["unit"], "U")

    def test_pdf_reconstructs_numbered_hierarchy_and_rejects_noise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "LOT 06 SERRURERIE.pdf"
            pdf = fitz.open()
            cover = pdf.new_page()
            cover.insert_text((50, 70), "BONDUELLE - BATIMENT GAY LUSSAC", fontsize=16)
            cover.insert_text((50, 110), "Architecte", fontsize=12)
            toc = pdf.new_page()
            toc.insert_text((50, 60), "SOMMAIRE", fontsize=16)
            toc.insert_text((50, 100), "3. DESCRIPTION DES OUVRAGES ........ 3", fontsize=11)
            work = pdf.new_page()
            work.insert_text((50, 70), "3.", fontsize=14)
            work.insert_text((105, 70), "DESCRIPTION DES OUVRAGES", fontsize=14)
            work.insert_text((50, 115), "3.1", fontsize=12)
            work.insert_text((105, 115), "ESCALIER EXTERIEUR", fontsize=12)
            work.insert_text((50, 145), "Epaisseur 12 mm et hauteur 4.1 m.", fontsize=10)
            work.insert_text((50, 190), "3.2", fontsize=12)
            work.insert_text((105, 190), "OPTION : GARDE-CORPS", fontsize=12)
            pdf.save(path)
            pdf.close()

            lot = parse_document(extract_document(path), "src_split")

        designations = {line["designation"] for line in lot["lines"]}
        self.assertNotIn("BONDUELLE - BATIMENT GAY LUSSAC", designations)
        self.assertNotIn("SOMMAIRE", designations)
        by_code = {line["code"]: line for line in lot["lines"]}
        self.assertEqual(by_code["3.1"]["designation"], "ESCALIER EXTERIEUR")
        self.assertEqual(by_code["3.1"]["quantity"], None)
        self.assertEqual(by_code["3.1"]["unit"], "U")
        self.assertFalse(by_code["3.2"]["included"])
        self.assertEqual(lot["perimeter"]["method"], "explicit_anchor")


class ExcelTests(unittest.TestCase):
    def test_export_matches_model_and_uses_correct_totals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "CCTP LOT 03.docx"
            make_docx(source)
            lot = parse_document(extract_document(source), "src_export")
            manual = {
                "id": "manual_1",
                "kind": "item",
                "level": 3,
                "code": "3.2.2",
                "designation": "Objet ajouté manuellement",
                "description": "",
                "unit": "U",
                "quantity": 2,
                "unit_price": None,
                "included": True,
                "confidence": 1,
                "review_status": "validated",
                "review_reason": "",
                "source_id": "src_export",
                "source_page": None,
                "source_excerpt": "Ajout manuel",
                "origin": "manual",
            }
            lot["lines"].append(manual)
            output = Path(directory) / "DPGF.xlsx"
            export_analysis(sample_analysis(lot), output)
            with ZipFile(output) as archive:
                logo_members = [
                    name
                    for name in archive.namelist()
                    if name.startswith("xl/media/")
                ]
                self.assertEqual(len(logo_members), 1)
                self.assertTrue(logo_members[0].endswith(".png"))
                self.assertTrue(
                    archive.read(logo_members[0]).startswith(
                        b"\x89PNG\r\n\x1a\n"
                    )
                )
            workbook = load_workbook(output, data_only=False)
            sheet = workbook.active

        self.assertIn("LOT 03", sheet["B8"].value)
        self.assertEqual(len(sheet._images), 1)
        self.assertEqual(sheet.row_dimensions[2].height, 51)
        values = {
            str(sheet.cell(row, 3).value): row
            for row in range(1, sheet.max_row + 1)
            if sheet.cell(row, 3).value
        }
        manual_row = values["Objet ajouté manuellement"]
        self.assertEqual(sheet.cell(manual_row, 7).value, f'=IF(OR(E{manual_row}="",F{manual_row}=""),"",E{manual_row}*F{manual_row})')
        self.assertFalse(sheet.cell(manual_row, 6).protection.locked)
        self.assertFalse(sheet.cell(manual_row, 5).protection.locked)
        self.assertIsNotNone(sheet.cell(manual_row, 4).comment)
        vat_row = next(row for label, row in values.items() if label.startswith("TVA à"))
        total_row = values["TOTAL € T.T.C."]
        self.assertIn(f"G{vat_row - 1}*20", sheet.cell(vat_row, 7).value)
        self.assertIn(f"G{vat_row - 1}", sheet.cell(total_row, 7).value)
        self.assertFalse(sheet.protection.sheet)

    def test_export_only_subtotals_x_x_and_styles_deeper_objects(self) -> None:
        def line(
            code: str,
            designation: str,
            kind: str,
            level: int,
            *,
            included: bool = True,
        ) -> dict:
            return {
                "id": f"line_{code}",
                "kind": kind,
                "level": level,
                "code": code,
                "designation": designation,
                "description": "",
                "unit": None if kind == "section" else "U",
                "quantity": None,
                "unit_price": None,
                "included": included,
                "confidence": 1,
                "review_status": "validated",
                "review_reason": "",
                "source_id": "src_tree",
                "source_page": 1,
                "source_excerpt": designation,
                "origin": "deterministic-v2",
                "unit_source": None if kind == "section" else "rule",
            }

        lot = {
            "id": "lot_tree",
            "code": "05",
            "title": "Menuiseries extérieures",
            "source_id": "src_tree",
            "warnings": [],
            "lines": [
                line("4", "DESCRIPTION DES OUVRAGES", "section", 1),
                line("4.5", "MUR RIDEAU ALUMINIUM", "section", 2),
                line("4.5.1", "Dépose mur rideau", "item", 3),
                line("4.5.2", "Remplacement vitrages", "item", 3),
                line("4.6", "Porte vitrée automatique", "item", 2),
                line("4.7", "ENSEMBLE TECHNIQUE", "section", 2),
                line("4.7.1", "SOUS-ENSEMBLE", "section", 3),
                line("4.7.1.1", "Composant", "item", 4),
                line("4.7.2", "Variante", "item", 3, included=False),
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "tree.xlsx"
            export_analysis(sample_analysis(lot), output)
            sheet = load_workbook(output, data_only=False).active

        rows_by_code = {
            str(sheet.cell(row, 2).value): row
            for row in range(12, sheet.max_row + 1)
            if sheet.cell(row, 2).value
        }
        rows_by_label = {
            str(sheet.cell(row, 3).value): row
            for row in range(12, sheet.max_row + 1)
            if sheet.cell(row, 3).value
        }
        self.assertLess(rows_by_label["Sous-total 4.5"], rows_by_code["4.6"])
        self.assertEqual(sheet.cell(rows_by_code["4.6"], 4).value, "U")
        self.assertTrue(sheet.cell(rows_by_code["4.6"], 2).font.bold)
        self.assertTrue(sheet.cell(rows_by_code["4.6"], 3).font.bold)
        self.assertLess(rows_by_code["4.6"], rows_by_label["Sous-total 4.6"])
        self.assertLess(rows_by_label["Sous-total 4.6"], rows_by_code["4.7"])
        self.assertEqual(
            sheet.cell(rows_by_label["Sous-total 4.6"], 7).value,
            f"=G{rows_by_code['4.6']}",
        )
        self.assertEqual(
            sheet.cell(rows_by_code["4.6"], 2).style_id,
            sheet.cell(rows_by_code["4.5"], 2).style_id,
        )
        self.assertFalse(sheet.cell(rows_by_code["4.5.1"], 3).font.bold)
        self.assertTrue(sheet.cell(rows_by_code["4.5.1"], 3).font.italic)
        self.assertEqual(sheet.cell(rows_by_code["4.5.1"], 3).font.sz, 8)
        self.assertEqual(sheet.cell(rows_by_code["4.5"], 3).font.sz, 9)
        self.assertEqual(sheet.cell(rows_by_code["4"], 3).font.sz, 9)
        self.assertNotIn("Sous-total 4.7.1", rows_by_label)
        self.assertTrue(sheet.cell(rows_by_code["4.7.1"], 3).font.italic)
        self.assertFalse(sheet.cell(rows_by_code["4.7.1"], 3).font.bold)
        self.assertFalse(sheet.cell(rows_by_code["4.7.2"], 2).font.bold)
        self.assertTrue(sheet.cell(rows_by_code["4.7.2"], 3).font.italic)
        self.assertLess(rows_by_code["4.7.2"], rows_by_label["Sous-total 4.7"])
        self.assertNotIn(
            f"G{rows_by_code['4.7.2']}",
            str(sheet.cell(rows_by_label["Sous-total 4.7"], 7).value),
        )
        total_row = rows_by_label["TOTAL € H.T. hors prorata"]
        self.assertNotIn(
            f"G{rows_by_label['Sous-total 4.5']}",
            str(sheet.cell(total_row, 7).value),
        )
        self.assertEqual(sheet.sheet_view.topLeftCell, "A1")
        self.assertFalse(any(sheet.row_dimensions[row].hidden for row in range(1, 9)))

    def test_export_creates_one_workbook_per_lot(self) -> None:
        def lot(code: str, title: str) -> dict:
            return {
                "id": f"lot_{code}",
                "code": code,
                "title": title,
                "source_id": f"src_{code}",
                "warnings": [],
                "lines": [
                    {
                        "id": f"line_{code}",
                        "kind": "item",
                        "level": 2,
                        "code": f"{int(code)}.1",
                        "designation": f"Objet du lot {code}",
                        "description": "",
                        "unit": "U",
                        "quantity": None,
                        "unit_price": None,
                        "included": True,
                        "confidence": 1,
                        "review_status": "validated",
                        "review_reason": "",
                        "source_id": f"src_{code}",
                        "source_page": 1,
                        "source_excerpt": title,
                        "origin": "deterministic-v2",
                        "unit_source": "rule",
                    }
                ],
            }

        lots = [lot("03", "Gros œuvre"), lot("04", "Menuiseries")]
        analysis = {
            **sample_analysis(lots[0]),
            "lots": lots,
            "stats": recompute_stats(lots),
        }
        with tempfile.TemporaryDirectory() as directory:
            outputs = export_analysis_files(analysis, Path(directory))
            labels = [
                load_workbook(output, data_only=False).active["B8"].value
                for output in outputs
            ]

        self.assertEqual(len(outputs), 2)
        self.assertEqual(len({output.name for output in outputs}), 2)
        self.assertIn("LOT 03", labels[0])
        self.assertIn("LOT 04", labels[1])


class ApiFlowTests(unittest.TestCase):
    def test_complete_history_edit_export_flow(self) -> None:
        client = TestClient(app)
        buffer = BytesIO()
        document = Document()
        document.add_heading("LOT 05 — MENUISERIES", level=1)
        document.add_heading("5.1 PORTES", level=2)
        document.add_heading("5.1.1 Bloc-porte intérieur", level=3)
        document.add_paragraph("Fourniture et pose à l'unité.")
        document.save(buffer)
        second_buffer = BytesIO()
        second_document = Document()
        second_document.add_heading("LOT 06 — PEINTURE", level=1)
        second_document.add_heading("6.1 PEINTURES INTÉRIEURES", level=2)
        second_document.add_heading("6.1.1 Peinture murale", level=3)
        second_document.add_paragraph("Préparation et peinture, unité de règlement : m².")
        second_document.save(second_buffer)
        response = client.post(
            "/api/v1/analyses",
            data={
                "project_name": "Projet test DPGF",
                "project_reference": "TEST-001",
                "phase": "DCE",
            },
            files=[
                (
                    "files",
                    (
                        "CCTP LOT 05.docx",
                        buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                ),
                (
                    "files",
                    (
                        "CCTP LOT 06.docx",
                        second_buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                ),
            ],
        )
        self.assertEqual(response.status_code, 202, response.text)
        analysis_id = response.json()["id"]
        try:
            detail = client.get(f"/api/v1/analyses/{analysis_id}")
            self.assertEqual(detail.status_code, 200, detail.text)
            payload = detail.json()
            self.assertIn(payload["status"], {"ready", "needs_review"})
            lot = payload["lots"][0]
            lot["lines"].append(
                {
                    "id": "manual_api",
                    "kind": "item",
                    "level": 3,
                    "code": "5.1.2",
                    "designation": "Objet manuel API",
                    "unit": "U",
                    "quantity": 4,
                    "included": True,
                    "review_status": "validated",
                    "origin": "manual",
                }
            )
            saved = client.put(
                f"/api/v1/analyses/{analysis_id}",
                json={"project": payload["project"], "lots": payload["lots"]},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertTrue(
                any(
                    line["designation"] == "Objet manuel API"
                    for line in saved.json()["lots"][0]["lines"]
                )
            )
            reprocessed = client.post(
                f"/api/v1/analyses/{analysis_id}/reprocess"
            )
            self.assertEqual(reprocessed.status_code, 202, reprocessed.text)
            refreshed = client.get(f"/api/v1/analyses/{analysis_id}").json()
            self.assertTrue(
                any(
                    line["designation"] == "Objet manuel API"
                    for line in refreshed["lots"][0]["lines"]
                )
            )
            revisions = list(
                (store.analysis_directory(analysis_id) / "revisions").glob("*.json")
            )
            self.assertEqual(len(revisions), 1)
            exported = client.post(f"/api/v1/analyses/{analysis_id}/export")
            self.assertEqual(exported.status_code, 200, exported.text)
            download = client.get(exported.json()["download_url"])
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.headers["content-type"], "application/zip")
            with ZipFile(BytesIO(download.content)) as archive:
                workbook_names = [
                    name for name in archive.namelist() if name.endswith(".xlsx")
                ]
                workbooks = [
                    load_workbook(
                        BytesIO(archive.read(name)),
                        data_only=False,
                    )
                    for name in workbook_names
                ]
            self.assertEqual(len(workbook_names), 2)
            self.assertTrue(
                any(
                    cell.value == "Objet manuel API"
                    for workbook in workbooks
                    for row in workbook.active.iter_rows()
                    for cell in row
                )
            )
            history = client.get("/api/v1/analyses")
            self.assertTrue(any(item["id"] == analysis_id for item in history.json()["analyses"]))
            tco = client.get(f"/api/v1/analyses/{analysis_id}/tco")
            self.assertEqual(tco.json()["schema_version"], "1.0")
        finally:
            client.delete(f"/api/v1/analyses/{analysis_id}")


if __name__ == "__main__":
    unittest.main()
