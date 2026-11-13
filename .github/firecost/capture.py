"""Capture stills and a time-series clip of the live Cost of Fire page for its opening screen."""
import asyncio, os, sys
from playwright.async_api import async_playwright
OUT = sys.argv[1]; FIRE = sys.argv[2] if len(sys.argv) > 2 else '2026-WAOWF-260406'
URL = 'https://redstonegis.com/FireCost/'
HIDE = '#title,.maplibregl-ctrl-bottom-right,.maplibregl-ctrl-top-right,#hint3d{display:none!important}'
async def main():
    os.makedirs(OUT + '/frames', exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'])
        pg = await b.new_page(viewport={'width': 1600, 'height': 1000})
        pg.on('pageerror', lambda e: print('PAGEERROR', e))
        await pg.goto(URL + '#' + FIRE); await pg.wait_for_timeout(4000)
        await pg.click('#enter')
        try: await pg.wait_for_selector('#veil.out', timeout=60000)
        except Exception as e: print('veil wait', e)
        await pg.wait_for_timeout(3000)
        if await pg.get_attribute('#play', 'aria-label') == 'Pause': await pg.click('#play')
        if await pg.is_checked('#tOrb'): await pg.uncheck('#tOrb')
        await pg.add_style_tag(content=HIDE)
        print('fire', await pg.inner_text('#fname'), '3d', await pg.is_checked('#t3d'))
        n = int(await pg.get_attribute('#scrub', 'max')) + 1
        async def step(i):
            await pg.evaluate("i=>{const s=document.querySelector('#scrub');s.value=i;s.dispatchEvent(new Event('input',{bubbles:true}))}", i)
        stage = pg.locator('.stage')
        # let tiles and terrain settle on the final extent before the series
        await step(n - 1); await pg.wait_for_timeout(25000)
        await stage.screenshot(path=OUT + '/fire_relief.jpg', quality=90, type='jpeg')
        k = max(1, -(-n // 48)); idx = list(range(0, n, k));
        if idx[-1] != n - 1: idx.append(n - 1)
        for j, i in enumerate(idx):
            await step(i); await pg.wait_for_timeout(900)
            await stage.screenshot(path=f'{OUT}/frames/f{j:03d}.jpg', quality=90, type='jpeg')
        print('frames', len(idx), 'of', n)
        await step(n - 1)
        await pg.click('input[name=base][value=imagery]', force=True); await pg.wait_for_timeout(25000)
        await stage.screenshot(path=OUT + '/fire_imagery.jpg', quality=90, type='jpeg')
        await pg.click('#all'); await pg.wait_for_timeout(4000)
        await stage.screenshot(path=OUT + '/national.jpg', quality=90, type='jpeg')
        await b.close()
asyncio.run(main())
