# src/services/extraction.py
import base64
import re
from typing import Any, Dict
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

class SourceExtraction:
    @staticmethod
    def clean_html(html: str) -> str:
        soup = BeautifulSoup(html or "", "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        # remove comments
        for comment in soup.find_all(string=lambda t: isinstance(t, type(soup.string)) and getattr(t, "strip", None) and str(t).strip().startswith("<!--")):
            try:
                comment.extract()
            except Exception:
                pass

        important_attrs = {
            "type",
            "name",
            "id",
            "placeholder",
            "required",
            "aria-label",
            "aria-labelledby",
            "aria-controls",
            "aria-expanded",
            "aria-haspopup",
            "aria-modal",
            "aria-selected",
            "role",
            "for",
            "href",
            "value",
            "data-testid",
        }
        for tag in soup.find_all(True):
            # drop class/style to reduce noise
            attrs = dict(tag.attrs or {})
            for k in list(attrs.keys()):
                if k not in important_attrs:
                    tag.attrs.pop(k, None)

        cleaned = str(soup)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r">\s+<", "><", cleaned)
        return cleaned

    @staticmethod
    def extract_main_elements(cleaned_html: str, limit: int = 24) -> Dict[str, Any]:
        """Very small, stable element summary for prompting."""
        soup = BeautifulSoup(cleaned_html or "", "html.parser")

        def pick_label(el) -> str:
            return (
                (el.get("aria-label") or "")
                or (el.get("placeholder") or "")
                or (el.get_text(" ", strip=True) or "")
                or (el.get("name") or "")
                or (el.get("id") or "")
            ).strip()[:120]

        out: Dict[str, Any] = {"buttons": [], "inputs": []}
        for el in soup.select("button"):
            item: Dict[str, Any] = {"label": pick_label(el), "tag": "button", "role": (el.get("role") or "")}
            if el.get("id"):
                item["id"] = el.get("id")
            if el.get("type"):
                item["type"] = el.get("type")
            out["buttons"].append(item)
            if len(out["buttons"]) >= limit:
                break

        for el in soup.select("input, textarea, select"):
            tag = el.name
            item = {
                "label": pick_label(el),
                "tag": tag,
                "role": (el.get("role") or ""),
            }
            if el.get("id"):
                item["id"] = el.get("id")
            if el.get("type"):
                item["type"] = el.get("type")
            if el.get("placeholder"):
                item["placeholder"] = el.get("placeholder")
            out["inputs"].append(item)
            if len(out["inputs"]) >= limit:
                break

        return {k: v for k, v in out.items() if v}

    @staticmethod
    async def _scan_ui_grouped_by_container(page, limit: int = 24) -> Dict[str, Any]:
        """Port of util.js grouping logic (runtime, visibility-aware)."""
        return await page.evaluate(
            """
            (limit) => {
              const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim()
              const isVisible = (el) => {
                if (!(el instanceof Element)) return false
                const style = window.getComputedStyle(el)
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false
                if (el.hasAttribute('hidden') || el.getAttribute('aria-hidden') === 'true') return false
                const rect = el.getBoundingClientRect()
                return rect.width > 0 && rect.height > 0
              }
              const getName = (el) =>
                normalize(
                  el.getAttribute('aria-label') ||
                  el.getAttribute('aria-labelledby') ||
                  el.innerText ||
                  el.textContent ||
                  el.getAttribute('placeholder') ||
                  el.getAttribute('name') ||
                  el.getAttribute('id') ||
                  ''
                )
              const getSelectorHints = (el) => ({
                id: el.getAttribute('id') || '',
                name: el.getAttribute('name') || '',
                placeholder: el.getAttribute('placeholder') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                dataTestId: el.getAttribute('data-testid') || '',
              })
              const getRole = (el) => {
                const role = (el.getAttribute('role') || '').toLowerCase()
                if (role) return role
                const tag = (el.tagName || '').toLowerCase()
                if (tag === 'button' || (tag === 'input' && ['button', 'submit'].includes((el.type || '').toLowerCase()))) return 'button'
                if (tag === 'select') return 'combobox'
                if (tag === 'a' && el.href) return 'link'
                if (tag === 'input' || tag === 'textarea') return 'textbox'
                if (tag === 'dialog') return 'dialog'
                if (tag === 'option') return 'option'
                return ''
              }
              const isClickableCardRoot = (el) => {
                const tag = (el.tagName || '').toLowerCase()
                if (!['div', 'article', 'li', 'section'].includes(tag)) return false
                const tid = (el.getAttribute('data-testid') || '').trim()
                if (!tid) return false
                if (tid === 'product-card' || tid === 'item-card') return true
                if (/-card$/i.test(tid) && !/(thumb|image|preview)$/i.test(tid)) return true
                return false
              }
              const labelForClickableCard = (el) => {
                const pick = el.querySelector?.('[data-testid=\"product-name\"], [data-testid=\"title\"], h1, h2, h3')
                if (pick) {
                  const t = normalize((pick.innerText || pick.textContent || '').trim())
                  if (t) return t.slice(0, 120)
                }
                const t = normalize((el.innerText || el.textContent || '').trim())
                return t.slice(0, 120)
              }
              const classify = (el) => {
                if (isClickableCardRoot(el)) return 'clickable_cards'
                const role = getRole(el)
                const tag = (el.tagName || '').toLowerCase()
                if (role === 'combobox' || tag === 'select') return 'dropdowns'
                if (role === 'option' || role === 'menuitem' || tag === 'option') return 'options'
                if (role === 'link' || (tag === 'a' && el.href)) return 'links'
                if ((tag === 'input' && !['hidden', 'button', 'submit'].includes((el.type || '').toLowerCase())) || tag === 'textarea') return 'inputs'
                if (role === 'button' || tag === 'button' || (tag === 'input' && ['button', 'submit'].includes((el.type || '').toLowerCase()))) return 'buttons'
                return ''
              }
              const groupFor = (el) => {
                const c = el.closest('dialog, [role=\"dialog\"], [role=\"alertdialog\"], form, table, main')
                if (!c) return { key: 'root', label: 'Main content', role: 'main', tag: 'body' }
                return {
                  key: c.id || `${(c.tagName || 'container').toLowerCase()}-${Math.abs((c.outerHTML || '').length % 100000)}`,
                  label: getName(c),
                  role: c.getAttribute('role') || '',
                  tag: (c.tagName || '').toLowerCase(),
                }
              }
              const empty = () => ({
                buttons: [],
                inputs: [],
                dropdowns: [],
                options: [],
                links: [],
                clickable_cards: [],
              })
              const out = {}
              const all = document.querySelectorAll(
                'button, a[href], input, select, textarea, option, [role=\"button\"], [role=\"link\"], [role=\"combobox\"], [role=\"textbox\"], [role=\"searchbox\"], [role=\"option\"], [role=\"menuitem\"], [data-testid=\"product-card\"], [data-testid=\"item-card\"], [data-testid$=\"-card\"]'
              )
              const seen = new Set()
              for (const el of all) {
                if (!isVisible(el)) continue
                const cat = classify(el)
                if (!cat) continue
                if (cat === 'clickable_cards' && !isClickableCardRoot(el)) continue
                const g = groupFor(el)
                if (!out[g.key]) out[g.key] = { label: g.label, role: g.role, tag: g.tag, ...empty() }
                const hints = getSelectorHints(el)
                const tag = (el.tagName || '').toLowerCase()
                const isCard = cat === 'clickable_cards'
                const item = {
                  label: isCard ? labelForClickableCard(el) : getName(el),
                  role: isCard ? 'clickable-card' : getRole(el),
                  tag,
                  id: hints.id,
                  name: hints.name,
                  placeholder: hints.placeholder,
                  aria_label: hints.ariaLabel,
                  data_testid: hints.dataTestId,
                }
                if (isCard && hints.dataTestId) {
                  item.interaction_hint =
                    `Whole tile is clickable — use page.getByTestId('${hints.dataTestId}').first() or .nth(i); optional: .filter({ hasText: '...' })`
                }
                const sig = `${g.key}|${cat}|${item.role}|${item.tag}|${item.label}`
                if (seen.has(sig)) continue
                seen.add(sig)
                if (out[g.key][cat].length < limit) out[g.key][cat].push(item)
              }
              return out
            }
            """,
            limit,
        )

    @staticmethod
    async def fetch(url: str, test_name: str = "", test_spec: str = "") -> Dict[str, Any]:
        """Static Playwright fetch: screenshot + cleaned html + grouped elements."""
        out: Dict[str, Any] = {
            "ok": False,
            "mode": "static_playwright",
            "strategy": "static_playwright",
            "errors": [],
            "page_screenshot": "",
            "cleaned_html": "",
            "grouped_elements": {},
            "main_elements": {},
            "prompt_context": "",
        }
        if not url:
            out["errors"].append("Missing test_url")
            return out

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 720})
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(1500)

                png_bytes = await page.screenshot(full_page=True)
                out["page_screenshot"] = base64.b64encode(png_bytes).decode("utf-8")

                html = await page.content()
                cleaned = SourceExtraction.clean_html(html)
                out["cleaned_html"] = cleaned
                out["main_elements"] = SourceExtraction.extract_main_elements(cleaned)
                out["grouped_elements"] = await SourceExtraction._scan_ui_grouped_by_container(page, limit=24)

                out["prompt_context"] = (
                    f"strategy: static_playwright\\n"
                    f"test_name: {test_name}\\n"
                    f"test_spec: {test_spec}\\n"
                )

                await browser.close()

            out["ok"] = True
            return out
        except Exception as e:
            out["errors"].append(str(e))
            return out