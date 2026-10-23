import pytest

from topside.config import load_config, param


def test_every_parameter_has_value_unit_source():
    def walk(node, key=""):
        if isinstance(node, dict) and "value" in node:
            assert node.get("unit"), f"{key} has no unit"
            assert node.get("source"), f"{key} has no source"
            # PyYAML reads 15.0e6 as a *string*; it must be written 15.0e+6
            if isinstance(node["value"], str):
                with pytest.raises(ValueError):
                    float(node["value"])
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{key}.{k}".strip("."))

    walk(load_config())


def test_param_errors():
    cfg = {"a": {"value": 1, "unit": "m", "source": ""}, "b": {"c": 2}}
    with pytest.raises(ValueError):
        param(cfg, "a")
    with pytest.raises(KeyError):
        param(cfg, "b.c")
    with pytest.raises(KeyError):
        param(cfg, "nope")
