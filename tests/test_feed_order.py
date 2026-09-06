import pytest
from bs4 import BeautifulSoup
from app.widgets.factory import generate_widget_html

def test_single_widget_prepends_at_top_of_grid():
    grid_html = '<div id="dashboard-grid" class="dashboard-grid"><div class="widget-container" id="w-old-1"><h3>Old 1</h3></div></div>'
    soup = BeautifulSoup(grid_html, "html.parser")
    target = soup.select_one('#dashboard-grid')
    
    new_node = BeautifulSoup('<div class="widget-container" id="w-new-2"><h3>New 2</h3></div>', "html.parser")
    target.insert(0, new_node)
    
    kids = [el.get('id') for el in target.find_all('div', class_='widget-container')]
    assert kids == ['w-new-2', 'w-old-1'], f"Expected newest widget at top, got: {kids}"

def test_batch_widgets_prepend_at_top_preserving_batch_order():
    grid_html = '<div id="dashboard-grid" class="dashboard-grid"><div class="widget-container" id="w-old-1"><h3>Old 1</h3></div></div>'
    soup = BeautifulSoup(grid_html, "html.parser")
    target = soup.select_one('#dashboard-grid')
    
    batch = [
        ('w-batch-1', '<div class="widget-container" id="w-batch-1"><h3>Batch 1</h3></div>'),
        ('w-batch-2', '<div class="widget-container" id="w-batch-2"><h3>Batch 2</h3></div>'),
    ]
    for i, (wid, html) in enumerate(batch):
        node = BeautifulSoup(html, "html.parser")
        target.insert(i, node)
        
    kids = [el.get('id') for el in target.find_all('div', class_='widget-container')]
    assert kids == ['w-batch-1', 'w-batch-2', 'w-old-1'], f"Expected batch at top in order, got: {kids}"

def test_inplace_update_replaces_existing_without_shuffling_feed():
    grid_html = '''<div id="dashboard-grid" class="dashboard-grid">
        <div class="widget-container" id="w-top"><h3>Top</h3></div>
        <div class="widget-container checklist" id="w-edit-target"><h3>Original Checklist</h3></div>
        <div class="widget-container" id="w-bottom"><h3>Bottom</h3></div>
    </div>'''
    soup = BeautifulSoup(grid_html, "html.parser")
    target = soup.select_one('#dashboard-grid')
    
    existing = soup.find(id="w-edit-target")
    assert existing is not None
    
    replacement = BeautifulSoup('<div class="widget-container checklist" id="w-edit-target"><h3>Updated Checklist</h3></div>', "html.parser")
    existing.replace_with(replacement)
    
    kids = [el.get('id') for el in target.find_all('div', class_='widget-container')]
    assert kids == ['w-top', 'w-edit-target', 'w-bottom'], "In-place edit should maintain existing slot"
    assert "Updated Checklist" in str(soup)
