"""Tests for models and exporters."""
from __future__ import annotations

import json
import sqlite3

import pytest

from supescrapper.models import ScrapedItem, ScrapedResultSet


class TestScrapedItem:
    def test_defaults(self):
        item = ScrapedItem()
        assert item.tag == ""
        assert item.text == ""
        assert item.html == ""
        assert item.attrs == {}
        assert item.url == ""
        assert item.meta == {}
        assert item.classification == []

    def test_getitem(self):
        item = ScrapedItem(text="hello", tag="p", url="/page", attrs={"class": "x"})
        assert item["text"] == "hello"
        assert item["tag"] == "p"
        assert item["url"] == "/page"
        assert item["class"] == "x"

    def test_getitem_meta_first(self):
        item = ScrapedItem(meta={"price": "$10"}, attrs={"price": "$5"})
        assert item["price"] == "$10"

    def test_to_dict(self):
        item = ScrapedItem(tag="a", text="Link", url="/page", attrs={"href": "/page"})
        d = item.to_dict()
        assert d["tag"] == "a"
        assert d["text"] == "Link"
        assert d["href"] == "/page"

    def test_to_dict_with_classification(self):
        item = ScrapedItem(tag="button", text="OK", classification=["button"])
        assert item.to_dict()["classification"] == ["button"]

    def test_to_dict_empty_classification_omitted(self):
        item = ScrapedItem(tag="div", text="Hello")
        assert "classification" not in item.to_dict()

    def test_to_dict_attrs_cannot_overwrite_core_fields(self):
        item = ScrapedItem(tag="div", text="real", url="/real",
                           attrs={"tag": "spoofed", "text": "spoofed", "url": "spoofed"})
        d = item.to_dict()
        assert d["tag"] == "div"
        assert d["text"] == "real"
        assert d["url"] == "/real"

    def test_to_dict_meta_cannot_overwrite_core_fields(self):
        item = ScrapedItem(tag="p", text="real", meta={"tag": "spoof"})
        assert item.to_dict()["tag"] == "p"

    def test_to_json(self):
        item = ScrapedItem(tag="p", text="Hello")
        data = json.loads(item.to_json())
        assert data["tag"] == "p"

    def test_from_dict(self):
        data = {"tag": "h1", "text": "Title", "url": "/home", "custom": "val"}
        item = ScrapedItem.from_dict(data)
        assert item.tag == "h1"
        assert item.text == "Title"
        assert item.url == "/home"
        assert item.meta.get("custom") == "val"

    def test_from_dict_with_classification(self):
        data = {"tag": "button", "text": "OK", "classification": ["button"]}
        item = ScrapedItem.from_dict(data)
        assert item.classification == ["button"]


class TestScrapedResultSet:
    def _make(self, *texts):
        return ScrapedResultSet([ScrapedItem(text=t, tag="p") for t in texts])

    def test_len_iter_getitem(self):
        rs = self._make("a", "b", "c")
        assert len(rs) == 3
        assert [i.text for i in rs] == ["a", "b", "c"]
        assert rs[0].text == "a"
        assert rs[-1].text == "c"

    def test_first_last(self):
        rs = self._make("a", "b")
        assert rs.first().text == "a"
        assert rs.last().text == "b"

    def test_first_last_empty(self):
        rs = ScrapedResultSet()
        assert rs.first() is None
        assert rs.last() is None

    def test_to_list_json(self):
        rs = self._make("x", "y")
        assert len(rs.to_list()) == 2
        assert len(json.loads(rs.to_json())) == 2

    def test_source_url(self):
        rs = self._make("a")
        rs.source_url = "https://example.com"
        assert rs.source_url == "https://example.com"


class TestExportJSON:
    def test_export(self, tmp_path):
        rs = ScrapedResultSet([ScrapedItem(tag="p", text="Hi")])
        out = tmp_path / "out.json"
        rs.export("json", out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data) == 1


class TestExportCSV:
    def test_export(self, tmp_path):
        rs = ScrapedResultSet([
            ScrapedItem(tag="a", text="L1", attrs={"href": "/1"}),
            ScrapedItem(tag="a", text="L2", attrs={"href": "/2"}),
        ])
        out = tmp_path / "out.csv"
        rs.export("csv", out)
        content = out.read_text(encoding="utf-8")
        assert "L1" in content

    def test_empty_export(self, tmp_path):
        rs = ScrapedResultSet()
        out = tmp_path / "empty.csv"
        rs.export("csv", out)
        assert out.read_text(encoding="utf-8") == ""


class TestExportSQLite:
    def test_export(self, tmp_path):
        rs = ScrapedResultSet([
            ScrapedItem(tag="p", text="A"),
            ScrapedItem(tag="p", text="B"),
        ])
        out = tmp_path / "out.db"
        rs.export("sqlite", out)
        conn = sqlite3.connect(str(out))
        rows = conn.execute("SELECT * FROM results").fetchall()
        conn.close()
        assert len(rows) == 2

    def test_dedup(self, tmp_path):
        items = [ScrapedItem(tag="p", text="A", url="/a"), ScrapedItem(tag="p", text="A2", url="/a")]
        rs = ScrapedResultSet(items)
        out = tmp_path / "dedup.db"
        rs.export("sqlite", out, unique_keys=["url"])
        conn = sqlite3.connect(str(out))
        count = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        conn.close()
        assert count == 1

    def test_empty_export(self, tmp_path):
        rs = ScrapedResultSet()
        out = tmp_path / "empty.db"
        result = rs.export("sqlite", out)
        assert result == out

    def test_hostile_attribute_name_does_not_crash(self, tmp_path):
        evil = ScrapedItem(tag="div", text="x",
                           attrs={'weird"name': "v", "semi;colon": "w"})
        rs = ScrapedResultSet([evil])
        out = tmp_path / "hostile.db"
        rs.export("sqlite", out)
        conn = sqlite3.connect(str(out))
        cols = [r[1] for r in conn.execute("PRAGMA table_info(results)").fetchall()]
        conn.close()
        assert 'weird"name' in cols

    def test_invalid_table_name_raises(self, tmp_path):
        from supescrapper.exceptions import ExportError
        rs = ScrapedResultSet([ScrapedItem(tag="p", text="A")])
        with pytest.raises(ExportError, match="Invalid table name"):
            rs.export("sqlite", tmp_path / "x.db", table='bad); DROP TABLE x; --')


class TestInvalidFormat:
    def test_raises(self):
        rs = ScrapedResultSet()
        with pytest.raises(ValueError, match="Unknown format"):
            rs.export("xml", "out.xml")
