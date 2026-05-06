"""
Google Maps Scraper — Fixed
============================
Strategy: Parse each card in the sidebar feed directly.
No clicking, no navigation, no new tabs.
All data (name, rating, category, address, phone, website, hours)
is extracted from the card elements themselves.

Run:
  python scraper.py
"""

import asyncio
import json
import re
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from playwright.async_api import async_playwright


# ─────────────────────────────────────────
SEARCH_QUERY = "restaurants in karachi"
MAX_RESULTS  = 20
OUTPUT_JSON  = "results.json"
OUTPUT_EXCEL = "results.xlsx"
# ─────────────────────────────────────────


async def scrape_google_maps(query: str, max_results: int) -> list[dict]:
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--lang=en-US"]
        )
        context = await browser.new_context(
            locale="en-US",
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        # ── 1. Open Google Maps ───────────────────────────────────────
        url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
        print(f"[+] Opening Google Maps: {query}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        # ── 2. Dismiss consent popup ──────────────────────────────────
        for text in ["Accept all", "Reject all", "Accept", "Agree"]:
            try:
                btn = page.locator(f'button:has-text("{text}")')
                if await btn.count() > 0:
                    await btn.first.click()
                    await page.wait_for_timeout(2000)
                    break
            except:
                pass

        # ── 3. Wait for feed ──────────────────────────────────────────
        print("[+] Waiting for results feed...")
        try:
            await page.wait_for_selector('div[role="feed"]', timeout=20000)
        except:
            print("[!] Feed not found — check browser window")
            await browser.close()
            return results

        # ── 4. Scroll to load more cards ──────────────────────────────
        print(f"[+] Scrolling to load up to {max_results} results...")
        feed = page.locator('div[role="feed"]')

        for i in range(12):
            await feed.evaluate("el => el.scrollBy(0, 1500)")
            await page.wait_for_timeout(1200)
            count = await page.locator('div[role="feed"] a[href*="/maps/place/"]').count()
            print(f"    Scroll {i+1}: {count} cards loaded")
            if count >= max_results:
                break

        # ── 5. Extract each card directly from the feed ───────────────
        # Each listing card is a <div> inside the feed that contains:
        # - an <a href="/maps/place/..."> with the URL
        # - nested spans/divs with name, rating, reviews, category, address
        #
        # We grab all card containers, then parse each one's inner text
        # using targeted child selectors — no clicking required.

        cards = page.locator('div[role="feed"] > div > div:has(a[href*="/maps/place/"])')
        total = min(await cards.count(), max_results)
        print(f"\n[+] Parsing {total} listing cards...\n")

        for i in range(total):
            try:
                card = cards.nth(i)

                # ── Name ─────────────────────────────────────────────
                name = None
                for sel in ['div.qBF1Pd', 'span.qBF1Pd', '.fontHeadlineSmall', 'div[aria-label]']:
                    try:
                        el = card.locator(sel).first
                        if await el.count() > 0:
                            name = (await el.inner_text(timeout=1000)).strip()
                            if name:
                                break
                    except:
                        pass

                # Fallback: get name from the aria-label of the card link
                if not name:
                    try:
                        link_el = card.locator('a[href*="/maps/place/"]').first
                        aria = await link_el.get_attribute("aria-label")
                        if aria:
                            name = aria.strip()
                    except:
                        pass

                # ── URL ──────────────────────────────────────────────
                url_val = None
                try:
                    link_el = card.locator('a[href*="/maps/place/"]').first
                    url_val = await link_el.get_attribute("href")
                except:
                    pass

                # ── Rating + Reviews from card raw text ───────────────
                # The card inner text has a line like: "4.2" or "4.2 (1,234)"
                # We parse both from the raw text to avoid selector brittleness.
                rating  = None
                reviews = None
                try:
                    raw_text = (await card.inner_text(timeout=2000)).strip()
                    for line in raw_text.splitlines():
                        line = line.strip()
                        # Match rating pattern: "4.2" or "4.2 (1,234)" or "4,2"
                        m = re.match(r"^(\d[\.,]\d)\s*\(?([0-9,\.K]+)?\)?$", line)
                        if m:
                            rating = float(m.group(1).replace(",", "."))
                            if m.group(2):
                                rev_str = m.group(2).replace(",", "").replace(".", "")
                                if rev_str.isdigit():
                                    reviews = int(rev_str)
                            break
                    # Also try aria-label on the rating element for review count
                    if not reviews:
                        el = card.locator('[aria-label*="review"]').first
                        if await el.count() > 0:
                            label = await el.get_attribute("aria-label") or ""
                            digits = re.sub(r"[^\d]", "", label)
                            if digits:
                                reviews = int(digits)
                except:
                    pass

                # ── Parse category + address from card inner text ─────
                # Card raw text looks like:
                #   Restaurant Name
                #   4.2
                #   Restaurant · short desc · Address, City
                #   Open · Closes 1 AM
                # We split by newline and parse each line by what it looks like.
                category = None
                address  = None
                try:
                    raw = (await card.inner_text(timeout=2000)).strip()
                    lines = [l.strip() for l in raw.splitlines() if l.strip()]
                    for line in lines:
                        # Skip lines that are the name, a pure rating, or hours
                        if line == name:
                            continue
                        if re.fullmatch(r"\d[\.,]\d", line):
                            continue
                        if re.search(r"Open|Closed|Opens|Closes", line):
                            continue
                        # Lines with "·" contain category and/or address
                        if "·" in line:
                            parts = [p.strip() for p in line.split("·") if p.strip()]
                            for part in parts:
                                # Category: letters only, short, no digits
                                if not category and not re.search(r"\d", part) and len(part) < 35:
                                    category = part
                                # Address: contains digits
                                elif not address and re.search(r"\d", part):
                                    address = part
                        # Lines with digits but no "·" — likely standalone address
                        elif not address and re.search(r"\d", line) and len(line) > 8:
                            address = line
                except:
                    pass

                # ── Hours ────────────────────────────────────────────
                # Hours text contains "Open" or "Closed" — scan all spans
                hours = None
                try:
                    all_spans = card.locator('span')
                    span_count = await all_spans.count()
                    for j in range(span_count):
                        text = (await all_spans.nth(j).inner_text(timeout=300)).strip()
                        if text and ("Open" in text or "Closed" in text or "closes" in text.lower() or "opens" in text.lower()):
                            hours = text
                            break
                except:
                    pass

                # ── Build result dict ─────────────────────────────────
                result = {
                    "name"        : name,
                    "address"     : address,
                    "phone"       : None,   # Not shown in feed cards — needs detail page
                    "website"     : None,   # Not shown in feed cards — needs detail page
                    "hours_status": hours,
                    "category"    : category,
                    "rating"      : rating,
                    "review_count": reviews,
                    "url"         : url_val,
                }

                results.append(result)
                print(f"  [{i+1}/{total}] {name or '(no name)'} | {rating} | {hours}")

            except Exception as e:
                print(f"  [{i+1}/{total}] ERROR: {e}")
                continue

        # ── 7. Click each card to get phone + website ─────────────────
        # These only appear in the detail panel, not in feed cards.
        print(f"[+] Fetching phone & website by clicking each listing...")

        for i, result in enumerate(results):
            try:
                # Find the card by matching its URL
                card_link = page.locator(f'a[href="{result["url"]}"]').first
                if await card_link.count() == 0:
                    # Try scrolling to find it
                    await feed.evaluate("el => el.scrollTo(0, 0)")
                    await page.wait_for_timeout(500)
                    card_link = page.locator(f'a[href="{result["url"]}"]').first

                await card_link.scroll_into_view_if_needed()
                await page.wait_for_timeout(300)
                await card_link.click()
                await page.wait_for_timeout(2500)

                # Wait for detail panel
                try:
                    await page.wait_for_selector('h1', timeout=6000)
                except:
                    await page.keyboard.press('Escape')
                    await page.wait_for_timeout(800)
                    continue

                # Phone
                try:
                    el = page.locator('[data-item-id*="phone:tel"]').first
                    if await el.count() > 0:
                        result['phone'] = (await el.inner_text(timeout=3000)).strip()
                except:
                    pass

                # Website
                try:
                    el = page.locator('[data-item-id="authority"]').first
                    if await el.count() > 0:
                        result['website'] = (await el.inner_text(timeout=3000)).strip()
                except:
                    pass

                print(f"  [{i+1}/{len(results)}] {result.get('name','?')[:30]} | {result.get('phone','—')} | {result.get('website','—')}")

                await page.keyboard.press('Escape')
                await page.wait_for_timeout(800)

            except Exception as e:
                print(f"  [{i+1}/{len(results)}] ERROR getting details: {e}")
                await page.keyboard.press('Escape')
                await page.wait_for_timeout(800)
                continue

        await browser.close()

    return results


def save_to_excel(data: list[dict], filename: str):
    """
    Save to Excel with:
    - Columns: #, Name, Address, Phone, Website, Hours, Category, Rating, Reviews, URL
    - Normal cell sizes (no wrap, standard row height)
    - Blue header row
    - Alternating light row colors
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Google Maps Results"

    # Column order — category/rating/reviews at the end as requested
    headers = [
        "#", "Name", "Address", "Phone", "Website",
        "Hours Status", "Category", "Rating", "Reviews", "URL"
    ]

    header_fill = PatternFill(start_color="1A73E8", end_color="1A73E8", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    alt_fill    = PatternFill(start_color="E8F0FE", end_color="E8F0FE", fill_type="solid")

    # Write headers
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill      = header_fill
        cell.font      = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Write data rows
    for row_i, item in enumerate(data, 2):
        row_data = [
            row_i - 1,
            item.get("name"),
            item.get("address"),
            item.get("phone"),
            item.get("website"),
            item.get("hours_status"),
            item.get("category"),
            item.get("rating"),
            item.get("review_count"),
            item.get("url"),
        ]
        for col_i, val in enumerate(row_data, 1):
            cell = ws.cell(row=row_i, column=col_i, value=val)
            cell.alignment = Alignment(vertical="center")  # No wrap_text
            if row_i % 2 == 0:
                cell.fill = alt_fill

    # Normal column widths — not too wide
    col_widths = [4, 22, 28, 15, 20, 18, 16, 8, 9, 40]
    for col, w in enumerate(col_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = w

    # Standard row heights (default is ~15)
    ws.row_dimensions[1].height = 18
    for row_i in range(2, len(data) + 2):
        ws.row_dimensions[row_i].height = 15

    import os
    target = filename
    base, ext = os.path.splitext(filename)
    counter = 1
    while True:
        try:
            wb.save(target)
            print(f"[+] Excel saved → {target}")
            if target != filename:
                print(f"    (Tip: close the old file in Excel next time)")
            break
        except PermissionError:
            target = f"{base}_{counter}{ext}"
            counter += 1


def save_to_json(data: list[dict], filename: str):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[+] JSON  saved → {filename}")


def print_summary(data: list[dict]):
    print("\n" + "="*65)
    print(f"  RESULTS — {len(data)} listings scraped")
    print("="*65)
    print(f"  {'#':<4} {'Name':<28} {'Rating':<8} {'Reviews':<10} {'Hours'}")
    print("-"*65)
    for i, item in enumerate(data, 1):
        name    = (item.get("name") or "N/A")[:26]
        rating  = str(item.get("rating") or "-")
        reviews = str(item.get("review_count") or "-")
        hours   = (item.get("hours_status") or "-")[:20]
        print(f"  {i:<4} {name:<28} {rating:<8} {reviews:<10} {hours}")
    print("="*65)


async def main():
    print("="*50)
    print("  Google Maps Scraper")
    print("="*50 + "\n")

    results = await scrape_google_maps(SEARCH_QUERY, MAX_RESULTS)

    if not results:
        print("\n[!] No results scraped.")
        return

    save_to_json(results, OUTPUT_JSON)
    save_to_excel(results, OUTPUT_EXCEL)
    print_summary(results)
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())