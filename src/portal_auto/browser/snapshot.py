"""A compact, LLM-friendly view of the page: visible text plus numbered interactive elements."""
from dataclasses import dataclass

REF_ATTRIBUTE = "data-pw-ref"
MAX_ELEMENTS = 80
MAX_TEXT_CHARS = 1500

_SNAPSHOT_JS = """
([attr, maxElements, maxText]) => {
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const selector = 'a[href],button,input,select,textarea,label,[role=button],[role=link],[role=checkbox],[role=tab],[role=menuitem],[onclick]';
  const isFileInput = el => el.tagName === 'INPUT' && el.type === 'file';
  const elements = [];
  for (const el of document.querySelectorAll(selector)) {
    const box = el.getBoundingClientRect(), style = getComputedStyle(el);
    const hidden = !box.width || !box.height || style.visibility === 'hidden' || style.display === 'none' || el.type === 'hidden';
    // File inputs are often hidden behind a styled label or button, so they are listed anyway; labels only for them.
    if (hidden && !isFileInput(el)) continue;
    if (el.tagName === 'LABEL' && !(el.control && isFileInput(el.control))) continue;
    const ref = elements.length + 1;
    el.setAttribute(attr, ref);
    const tag = el.tagName.toLowerCase(), type = el.type || '';
    // Typed values never leave the page: only buttons expose their value attribute as a label.
    const isButtonInput = tag === 'input' && ['submit', 'button', 'reset'].includes(type);
    const shownText = isButtonInput ? el.value : (['input', 'textarea', 'select'].includes(tag) ? '' : el.innerText);
    const label = el.id && document.querySelector('label[for="' + el.id + '"]');
    elements.push({
      ref, tag: el.tagName.toLowerCase(), type: el.type || '', role: el.getAttribute('role') || '',
      id: el.id || '', testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || '',
      label: (label && label.innerText.trim()) || el.getAttribute('aria-label') || '',
      placeholder: el.getAttribute('placeholder') || '', name: el.getAttribute('name') || '',
      text: (shownText || '').trim().slice(0, 60), hidden,
    });
    if (elements.length >= maxElements) break;
  }
  return {title: document.title, url: location.href, text: document.body.innerText.slice(0, maxText), elements};
}
"""


@dataclass(frozen=True)
class Element:
    ref: int
    tag: str
    type: str
    role: str
    id: str
    testid: str
    label: str
    placeholder: str
    name: str
    text: str
    hidden: bool = False

    @property
    def description(self) -> str:
        return self.label or self.placeholder or self.text or self.name or self.id


@dataclass(frozen=True)
class Snapshot:
    url: str
    title: str
    text: str
    elements: dict[int, Element]

    def render(self) -> str:
        lines = [f"URL: {self.url}", f"Title: {self.title}", "Visible text:", self.text, "Interactive elements:"]
        lines += [f"[{e.ref}] <{e.tag} type={e.type}{' hidden' if e.hidden else ''}> {e.description}"
                  for e in self.elements.values()]
        return "\n".join(lines)


def take_snapshot(page) -> Snapshot:
    raw = page.evaluate(_SNAPSHOT_JS, [REF_ATTRIBUTE, MAX_ELEMENTS, MAX_TEXT_CHARS])
    elements = {e["ref"]: Element(**e) for e in raw["elements"]}
    return Snapshot(raw["url"], raw["title"], raw["text"], elements)
