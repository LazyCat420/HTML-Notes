from app.agents.auditor import audit_html_fragment

def test_auditor_valid_html():
    valid_html = """
    <article>
        <header>
            <p data-timestamp="2026-06-23T18:00:00Z">Project Update</p>
        </header>
        <section>
            <p>This is a <strong>valid</strong> note fragment.</p>
            <ul>
                <li>Item 1</li>
                <li>Item 2</li>
            </ul>
            <blockquote>Some quote</blockquote>
            <hr>
            <pre><code>print('hello')</code></pre>
            <span data-tag="tech">Tech topic</span>
            <a href="#note_123">Link to note 123</a>
        </section>
    </article>
    """
    res = audit_html_fragment(valid_html)
    assert res["is_valid"] is True
    assert len(res["errors"]) == 0

def test_auditor_invalid_tags():
    invalid_html = """
    <article>
        <script>alert('dangerous')</script>
        <p>Text</p>
    </article>
    """
    res = audit_html_fragment(invalid_html)
    assert res["is_valid"] is False
    assert any("Forbidden HTML tag: <script>" in err for err in res["errors"])

def test_auditor_invalid_attributes():
    invalid_html = """
    <article onclick="alert('dangerous')">
        <p>Text</p>
    </article>
    """
    res = audit_html_fragment(invalid_html)
    assert res["is_valid"] is False
    assert any("Forbidden attribute" in err for err in res["errors"])

def test_auditor_layout_elements():
    layout_html = """
    <div class="dashboard-grid flex-row">
        <aside class="sidebar" style="width: 250px;">
            <p>Sidebar content</p>
        </aside>
        <div class="main-content flex-col">
            <h3 class="glass-card-title">Main Content</h3>
            <table class="data-table">
                <thead>
                    <tr><th>Item</th><th>Value</th></tr>
                </thead>
                <tbody>
                    <tr><td>A</td><td>10</td></tr>
                </tbody>
            </table>
        </div>
    </div>
    """
    res = audit_html_fragment(layout_html)
    assert res["is_valid"] is True
    assert len(res["errors"]) == 0

def test_auditor_invalid_links():
    invalid_html = """
    <article>
        <a href="https://google.com">External Link</a>
    </article>
    """
    res = audit_html_fragment(invalid_html)
    assert res["is_valid"] is False
    assert any("Invalid link href target" in err for err in res["errors"])
