import pytest

from app.titles import TitleFilter


def test_include_exclude():
    tf = TitleFilter(include=("engineer",), exclude=("intern",))
    assert tf.matches("Staff Engineer")
    assert tf.matches("Engineering Manager")
    assert not tf.matches("Engineering Intern")
    assert not tf.matches("Head of Sales")
    assert TitleFilter(exclude=("intern",)).matches("Internal Tools Lead")
    assert not TitleFilter().matches("")


def test_seniority_and_rank():
    tf = TitleFilter(seniority=("vp", "director", "c_level"))
    assert tf.matches("VP, Sales")
    assert tf.matches("Vice President of Marketing")
    assert tf.matches("Director of Engineering")
    assert tf.matches("Chief Executive Officer")
    assert not tf.matches("Senior Software Engineer")
    assert not TitleFilter(seniority=("c_level",)).matches("Vice President, Sales")
    assert tf.rank("CEO") < tf.rank("VP Sales") < tf.rank("Director of X") < tf.rank("Engineer")


def test_bad_seniority_and_server_query():
    with pytest.raises(ValueError):
        TitleFilter(seniority=("boss",))
    assert TitleFilter(include=("sales", "marketing")).server_query() == '"sales" OR "marketing"'
