from app import workflow


def test_numeric_equation_is_normalized_to_root():
    assert workflow.normalize_kind("equation","numeric","root")=="root"
    assert workflow.KIND_TOOL[workflow.normalize_kind("equation","numeric","root")]=="find_root"


def test_symbolic_root_is_normalized_to_equation():
    assert workflow.normalize_kind("root","symbolic","equation")=="equation"
