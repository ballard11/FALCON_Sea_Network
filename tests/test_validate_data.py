"""
The data checks catch bad rows and name them. Each test copies the real data
model to a temporary folder, breaks one thing, and checks the message.

    python -m unittest discover tests
"""
import contextlib
import csv
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import validate_data  # noqa: E402

DATA = validate_data.DATA


class BrokenData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clean = validate_data.validate(DATA)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = os.path.join(self.tmp, "neo4j-datamodel")
        shutil.copytree(DATA, self.dir)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def edit(self, name, row, field, value):
        """Set one field of one data row (row 2 is the first after the header)."""
        path = os.path.join(self.dir, name)
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols, rows = reader.fieldnames, list(reader)
        rows[row - 2][field] = value
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)

    def problems(self):
        return validate_data.validate(self.dir)

    def assertProblem(self, *words):
        found = self.problems()
        self.assertTrue(any(all(w in p for w in words) for p in found),
                        "no problem mentions %s; got %s" % (words, found[:5]))

    def test_the_real_data_passes(self):
        self.assertEqual(self.clean, [])

    def test_bad_latitude_names_file_row_and_field(self):
        self.edit("nodes/ports.csv", 5, "lat", "north")
        self.assertProblem("nodes/ports.csv row 5", "lat 'north'")

    def test_out_of_range_coordinates(self):
        self.edit("nodes/sea_nodes.csv", 2, "lon", "200")
        self.assertProblem("nodes/sea_nodes.csv row 2", "not a valid position")

    def test_duplicate_id(self):
        self.edit("nodes/ports.csv", 3, "port_id", "WPI-16010")     # San Diego's id
        self.assertProblem("nodes/ports.csv row", "WPI-16010 is used more than once")

    def test_link_to_a_missing_node(self):
        self.edit("relationships/sea_segments.csv", 2, "to_sea_node_id", "SN-NOPE")
        self.assertProblem("relationships/sea_segments.csv row 2", "SN-NOPE", "not in nodes/sea_nodes.csv")

    def test_negative_length(self):
        self.edit("relationships/sea_segments.csv", 4, "length_nm", "-3")
        self.assertProblem("relationships/sea_segments.csv row 4", "length_nm '-3'")

    def test_length_that_disagrees_with_the_line(self):
        self.edit("relationships/sea_segments.csv", 2, "length_nm", "50")
        self.assertProblem("relationships/sea_segments.csv row 2", "length_nm is 50.0 but the line is")

    def test_unknown_passage(self):
        self.edit("relationships/sea_segments.csv", 2, "passage", "atlantis")
        self.assertProblem("passage 'atlantis' is not in nodes/passages.csv")

    def test_unreadable_geometry(self):
        self.edit("relationships/port_to_port.csv", 2, "geometry_wkt", "POINT(1 2)")
        self.assertProblem("relationships/port_to_port.csv row 2", "geometry_wkt is not a LINESTRING")

    def test_bad_harbor_size(self):
        self.edit("nodes/ports.csv", 2, "harbor_size", "Huge")
        self.assertProblem("nodes/ports.csv row 2", "harbor_size 'Huge'")

    def test_runway_for_a_missing_airfield(self):
        self.edit("nodes/runways.csv", 2, "airfield_id", "NOPE")
        self.assertProblem("nodes/runways.csv row 2", "airfield_id NOPE")

    def test_missing_rows_are_caught_by_the_count(self):
        path = os.path.join(self.dir, "nodes", "sea_nodes.csv")
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines[:-10])
        self.assertProblem("nodes/sea_nodes.csv", "but build-report.json says the build wrote 4525")

    def test_missing_column(self):
        path = os.path.join(self.dir, "nodes", "ports.csv")
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace("harbor_size", "size", 1))
        self.assertProblem("nodes/ports.csv: missing column harbor_size")

    def test_missing_file(self):
        os.remove(os.path.join(self.dir, "relationships", "port_to_port.csv"))
        self.assertProblem("relationships/port_to_port.csv: file is missing")

    def test_check_raises_with_every_problem(self):
        self.edit("nodes/ports.csv", 5, "lat", "north")
        with self.assertRaises(validate_data.DataError) as ctx:
            validate_data.check(self.dir)
        self.assertIn("nodes/ports.csv row 5", str(ctx.exception))


    def test_blank_id_and_blank_name(self):
        self.edit("nodes/sea_nodes.csv", 2, "sea_node_id", "  ")
        self.edit("nodes/passages.csv", 2, "name", "")
        self.assertProblem("nodes/sea_nodes.csv row 2 (no id): sea_node_id is blank")
        self.assertProblem("nodes/passages.csv row 2", "name is blank")

    def test_infinite_length(self):
        self.edit("relationships/sea_segments.csv", 3, "length_nm", "inf")
        self.assertProblem("relationships/sea_segments.csv row 3", "length_nm 'inf'")

    def test_a_line_that_starts_away_from_its_node(self):
        path = os.path.join(self.dir, "relationships", "sea_segments.csv")
        with open(path, newline="", encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f))
        wkt = row["geometry_wkt"]
        lon, lat = wkt[len("LINESTRING("):].split(",")[0].split()
        self.edit("relationships/sea_segments.csv", 2, "geometry_wkt",
                  wkt.replace("%s %s" % (lon, lat), "%s %s" % (lon, float(lat) + 0.1), 1))
        self.assertProblem("relationships/sea_segments.csv row 2", "the line starts or ends", "from its nodes")

    def test_geometry_shapes_that_are_not_a_line(self):
        for wkt in ("LINESTRING(1 2)", "LINESTRING(1 2,3 4)", "LINESTRING(1 2 3, 4 5 6)", "LINESTRING(1 2, 3 north)",
                    "LINESTRING(1 2, 300 4)", "", "linestring(1 2, 3 4)"):
            with self.subTest(wkt=wkt):
                self.assertIsNone(validate_data._line(wkt))
        self.assertEqual(validate_data._line("LINESTRING(1 2, 3 4)"), [(1.0, 2.0), (3.0, 4.0)])

    def test_numbers(self):
        self.assertEqual(validate_data._number("2.5"), 2.5)
        for v in ("", None, "nan", "inf", "-inf", "x", [1]):
            with self.subTest(value=v):
                self.assertIsNone(validate_data._number(v))

    def test_a_port_with_no_link_to_the_sea(self):
        for name in ("relationships/port_to_sea_node.csv", "relationships/port_to_port.csv"):
            path = os.path.join(self.dir, name)
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                cols, rows = reader.fieldnames, [r for r in reader if "WPI-16010" not in (
                    r.get("port_id"), r.get("from_port_id"), r.get("to_port_id"))]
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                w.writerows(rows)
        self.assertProblem("port WPI-16010 has no link to the sea network")

    def test_missing_build_report(self):
        os.remove(os.path.join(self.dir, "build-report.json"))
        self.assertProblem("build-report.json: file is missing")

    def test_many_problems_of_one_kind_are_counted_not_listed(self):
        path = os.path.join(self.dir, "nodes", "ports.csv")
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols, rows = reader.fieldnames, list(reader)
        for r in rows[:30]:
            r["harbor_size"] = "Huge"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        found = self.problems()
        self.assertEqual(sum("harbor_size 'Huge'" in p for p in found), validate_data.MAX_PER_CHECK)
        self.assertIn("... and 10 more like that (bad value)", found)

    def test_an_empty_folder(self):
        empty = os.path.join(self.tmp, "empty")
        os.mkdir(empty)
        found = validate_data.validate(empty)
        for name in list(validate_data.NODE_FILES) + list(validate_data.LINK_FILES) + ["build-report.json"]:
            self.assertTrue(any(p.startswith(name + ":") for p in found), name)

    def test_a_file_saved_with_a_byte_order_mark_is_fine(self):
        path = os.path.join(self.dir, "nodes", "ports.csv")
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(text)
        self.assertEqual(self.problems(), [])


class CommandLine(unittest.TestCase):
    def run_main(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = validate_data.main(args)
        return code, out.getvalue()

    def test_good_data_exits_0(self):
        code, printed = self.run_main([])
        self.assertEqual(code, 0)
        self.assertIn("passed every check", printed)

    def test_bad_data_exits_1_and_lists_the_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, printed = self.run_main([tmp])
        self.assertEqual(code, 1)
        self.assertIn("FAILED: 9 problems", printed)
        self.assertIn("nodes/ports.csv: file is missing", printed)

    def test_check_is_quiet_on_good_data(self):
        self.assertIsNone(validate_data.check())


if __name__ == "__main__":
    unittest.main()
