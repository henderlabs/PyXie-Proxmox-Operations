from pyxie_core.pool_workflow import plan_members, validate_pool_id


def test_pool_id():
    assert validate_pool_id("web-tier", set()) == []
    assert validate_pool_id("a", set()) and validate_pool_id("-x", set()) and validate_pool_id("a b", set())
    assert "already exists" in validate_pool_id("web", {"web"})[0]


def test_plan_members():
    cur = {"a": {1, 2}, "b": {3}}
    assert plan_members(cur, "a", [4, 1], [2]) == ([4], [2], [])
    new, gone, errs = plan_members(cur, "a", [3], [9])
    assert new == [] and gone == [] and len(errs) == 2 and "not in 'a'" in errs[0] and "already in pool 'b'" in errs[1]
    assert plan_members(cur, "a", [1], [])[2] == ["nothing to change"]
