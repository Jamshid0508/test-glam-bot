import sys
sys.stdout.reconfigure(encoding='utf-8')

with open('static/index.html', encoding='utf-8') as f:
    content = f.read()

# 1. Update restock form - add product info card and widen select
old1 = 'id="branch-restock-form" class="form-grid">\n          <label class="field">Gilam<select id="restock-product"></select></label>\n          <label class="field wide">Raqamlar (vergul yoki yangi qator bilan)<textarea id="restock-numbers" rows="2" placeholder="010, 011"></textarea></label>\n          <div class="field"><label>&nbsp;</label><button class="button" type="submit">➕ Qo\u2019shish</button></div>\n        </form>'

new1 = 'id="branch-restock-form" class="form-grid">\n          <label class="field wide">Gilam tanlang<select id="restock-product" style="font-size:14px"></select></label>\n          <div id="restock-product-info" style="display:none;background:#f0f7f0;border:1px solid #c3d9c3;border-radius:10px;padding:12px 16px;margin-bottom:4px;flex-wrap:wrap;gap:8px 20px;font-size:13px;grid-column:1/-1">\n            <span><span style="color:#666">Nomi:</span> <strong id="rpi-name">\u2014</strong></span>\n            <span><span style="color:#666">O\u02bcl\u010dami:</span> <strong id="rpi-dim">\u2014</strong></span>\n            <span><span style="color:#666">Sotish narxi:</span> <strong id="rpi-price">\u2014</strong></span>\n            <span><span style="color:#666">Qoldig\u02bbi:</span> <strong id="rpi-qty">\u2014</strong></span>\n          </div>\n          <label class="field wide">Raqamlar (vergul yoki yangi qator bilan)<textarea id="restock-numbers" rows="2" placeholder="010, 011"></textarea></label>\n          <div class="field"><label>&nbsp;</label><button class="button primary" type="submit">➕ Qo\u02bcshish</button></div>\n        </form>'

if old1 in content:
    print('Chunk 1 found, replacing...')
    content = content.replace(old1, new1, 1)
else:
    print('Chunk 1 NOT found, trying alternate...')
    # Try with &nbsp; entity
    old1b = 'id="branch-restock-form" class="form-grid">\n          <label class="field">Gilam<select id="restock-product"></select></label>'
    if old1b in content:
        print('Alt chunk 1b found')
        new1b = 'id="branch-restock-form" class="form-grid">\n          <label class="field wide">Gilam tanlang<select id="restock-product" style="font-size:14px"></select></label>\n          <div id="restock-product-info" style="display:none;background:#f0f7f0;border:1px solid #c3d9c3;border-radius:10px;padding:12px 16px;margin-bottom:4px;flex-wrap:wrap;gap:8px 20px;font-size:13px;grid-column:1/-1">\n            <span><span style="color:#666">Nomi:</span> <strong id="rpi-name">\u2014</strong></span>\n            <span><span style="color:#666">O\u02bcl\u010dami:</span> <strong id="rpi-dim">\u2014</strong></span>\n            <span><span style="color:#666">Sotish narxi:</span> <strong id="rpi-price">\u2014</strong></span>\n            <span><span style="color:#666">Qoldig\u02bbi:</span> <strong id="rpi-qty">\u2014</strong></span>\n          </div>'
        content = content.replace(old1b, new1b, 1)
        # Also update the button
        old_btn = '<button class="button" type="submit">➕ Qo\u2019shish</button>'
        new_btn = '<button class="button primary" type="submit">➕ Qo\u2019shish</button>'
        content = content.replace(old_btn, new_btn, 1)
        print('Applied alt chunks')
    else:
        print('Nothing matched for chunk 1')

# 2. Add updateRestockInfo function - add it before the branch-restock-form submit listener
old2 = '      $(\"#branch-restock-form\").addEventListener(\"submit\"'
new2 = '''      function updateRestockInfo() {
        const pr = (state.branchProducts || []).find(p => String(p.id) === String($(\"#restock-product\").value));
        const infoEl = $(\"#restock-product-info\");
        if (!pr) { infoEl.style.display = \"none\"; return; }
        infoEl.style.display = \"flex\";
        $(\"#rpi-name\").textContent = pr.name || \"\u2014\";
        $(\"#rpi-dim\").textContent = pr.dimensions ? (pr.dimensions + (pr.area_sqm ? \" (\" + pr.area_sqm + \" m\u00b2)\" : \"\")) : \"\u2014\";
        $(\"#rpi-price\").textContent = pr.sale_price ? new Intl.NumberFormat(\"uz-UZ\", {maximumFractionDigits:0}).format(pr.sale_price) + \" so\u02bcm\" : \"\u2014\";
        $(\"#rpi-qty\").textContent = new Intl.NumberFormat(\"uz-UZ\", {maximumFractionDigits:2}).format(Number(pr.quantity || 0)) + \" \" + (pr.unit || \"\");
      }
      $(\"#restock-product\").addEventListener(\"change\", updateRestockInfo);
      $(\"#branch-restock-form\").addEventListener(\"submit\"'''

if old2 in content:
    print('Chunk 2 found, replacing...')
    content = content.replace(old2, new2, 1)
else:
    print('Chunk 2 NOT found')

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done! File saved.')
