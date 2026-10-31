# Car Hunter browser extension

Click it on any car advert and it reads that page (only when you click; nothing is sent anywhere),
shows what it found, and can open your dashboard's "Add car" form filled in.

## Install (Chrome, Edge or Brave on a computer)
1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode**.
3. **Load unpacked** -> choose this `extension` folder.
4. Pin it: puzzle-piece icon -> pin "Car Hunter".

After changing these files, press the reload arrow on the extension's card.

## How it reads an advert
1. The page's structured listing data (schema.org JSON-LD `Car` / `Vehicle` / `Product` + `Offer`, Open Graph).
2. Otherwise the page's text: first £ price that isn't finance, mileage ("86k miles"), year, gearbox, fuel.
3. Registration: a current-format UK plate (AB12 CDE) written on the page; several = shown as "possible".
4. Photos: structured images, the Open Graph image, then large pictures on the page.

Permissions: `activeTab` + `scripting` only, so it can read just the tab you clicked it on.
