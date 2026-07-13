from shared.search_policy import decide_search


def test_hi_does_not_search_even_in_web_mode():
    d = decide_search("hi", web_mode=True)
    assert d.should_search is False


def test_search_explicit_does_search():
    d = decide_search("search the web for current GST e-invoice rules")
    assert d.should_search is True


def test_patent_does_search():
    d = decide_search("find patents for fluconazole process")
    assert d.should_search is True


def test_normal_chat_no_search():
    d = decide_search("how should I improve SHIMS as an agent?")
    assert d.should_search is False
