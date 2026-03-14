# Changelog

## [1.5.0](https://github.com/fizzi01/Perpetua/compare/v1.4.0...v1.5.0) (2026-03-14)


### Features

* **daemon:** implement live accessibility permission checks on macOS ([#62](https://github.com/fizzi01/Perpetua/issues/62)) ([7d19528](https://github.com/fizzi01/Perpetua/commit/7d19528e053e294625c11ac69ced7cddd726450d))


### Bug Fixes

* **daemon:** force exit when process remain pending on stop ([7d19528](https://github.com/fizzi01/Perpetua/commit/7d19528e053e294625c11ac69ced7cddd726450d))
* **gui:** fix titlebar buttons not working on Wayland ([#60](https://github.com/fizzi01/Perpetua/issues/60)) ([7475b39](https://github.com/fizzi01/Perpetua/commit/7475b39eac65fd382e37a7be7ea59386ea5f29c4))

## [1.4.0](https://github.com/fizzi01/Perpetua/compare/v1.3.0...v1.4.0) (2026-03-10)


### Features

* append timestamp to temporary configuration file names for uniqueness ([9c30a1b](https://github.com/fizzi01/Perpetua/commit/9c30a1b79dcc00638e825252c14f7590c3c04997))
* complete Wayland support for GNOME and KDE ([#58](https://github.com/fizzi01/Perpetua/issues/58)) ([a24dced](https://github.com/fizzi01/Perpetua/commit/a24dced19373a649105ca38d9a0fcaea3c34b7c1))
* **daemon:** force shutdown on multiple signals ([640dc1e](https://github.com/fizzi01/Perpetua/commit/640dc1eb0fb62807f1d27ea38ea747dedef7c4e1))
* enhance client hostname validation during handshake ([422ce61](https://github.com/fizzi01/Perpetua/commit/422ce61bcd21d71ab24e05a3cc65eb5c8351421c))
* enhance clipboard functionalities with file list detection ([#53](https://github.com/fizzi01/Perpetua/issues/53)) ([255fb7a](https://github.com/fizzi01/Perpetua/commit/255fb7af70cf1d83ec36b39d6fd45ffa41e0d048))
* extend Wayland support client side ([#57](https://github.com/fizzi01/Perpetua/issues/57)) ([f4caa4d](https://github.com/fizzi01/Perpetua/commit/f4caa4dac4146ca87137a610f9e86a231dd8dd1c))
* **gui:** add local IP address retrieval and display in client and server tab ([5ae9744](https://github.com/fizzi01/Perpetua/commit/5ae9744dc18d80b335ae21ac648d21c3b55f82d5))
* **gui:** improve logging parsing and formatting ([5ae9744](https://github.com/fizzi01/Perpetua/commit/5ae9744dc18d80b335ae21ac648d21c3b55f82d5))
* implement hotkey functionality for screen switching and panic actions ([#56](https://github.com/fizzi01/Perpetua/issues/56)) ([5019296](https://github.com/fizzi01/Perpetua/commit/5019296413a85185d86b1fa8cc4abdf8aa598be7))
* **mouse:** enhance barrier activation handling and prevent spurious recaptures (Wayland) ([#59](https://github.com/fizzi01/Perpetua/issues/59)) ([640dc1e](https://github.com/fizzi01/Perpetua/commit/640dc1eb0fb62807f1d27ea38ea747dedef7c4e1))
* removed wx dependency on Linux, reducing build time and size ([a24dced](https://github.com/fizzi01/Perpetua/commit/a24dced19373a649105ca38d9a0fcaea3c34b7c1))
* support multiple IP addresses for clients and improve handshake ([#54](https://github.com/fizzi01/Perpetua/issues/54)) ([5ae9744](https://github.com/fizzi01/Perpetua/commit/5ae9744dc18d80b335ae21ac648d21c3b55f82d5))


### Bug Fixes

* adjust daemon shutdown handling ([8f8e6ab](https://github.com/fizzi01/Perpetua/commit/8f8e6ab81dcddc27b36dc2c64d6a5b673e3589d3))
* **daemon:** handle connection errors when sending rejection messages ([a24dced](https://github.com/fizzi01/Perpetua/commit/a24dced19373a649105ca38d9a0fcaea3c34b7c1))
* **gui:** fix logging auto scroll ([5ae9744](https://github.com/fizzi01/Perpetua/commit/5ae9744dc18d80b335ae21ac648d21c3b55f82d5))
* handle error during stream close ([5019296](https://github.com/fizzi01/Perpetua/commit/5019296413a85185d86b1fa8cc4abdf8aa598be7))
* **keyboard:** fix complex hotkeys not working in client uinput backend ([f4caa4d](https://github.com/fizzi01/Perpetua/commit/f4caa4dac4146ca87137a610f9e86a231dd8dd1c))
* **keyboard:** fix keyboard hotkeys parsing on Linux ([a24dced](https://github.com/fizzi01/Perpetua/commit/a24dced19373a649105ca38d9a0fcaea3c34b7c1))
* **keyboard:** force controller uinput backend for linux ([5019296](https://github.com/fizzi01/Perpetua/commit/5019296413a85185d86b1fa8cc4abdf8aa598be7))
* **keyboard:** update wrong caps lock state management in client controller ([f4caa4d](https://github.com/fizzi01/Perpetua/commit/f4caa4dac4146ca87137a610f9e86a231dd8dd1c))

## [1.3.0](https://github.com/fizzi01/Perpetua/compare/v1.2.4...v1.3.0) (2026-02-28)


### Features

* Add Linux support ([#52](https://github.com/fizzi01/Perpetua/issues/52)) ([d251016](https://github.com/fizzi01/Perpetua/commit/d25101647d55f11ac81352d045efaa834832ec82))


### Bug Fixes

* **client:** improve abort handling ([#48](https://github.com/fizzi01/Perpetua/issues/48)) ([9823faa](https://github.com/fizzi01/Perpetua/commit/9823faacab10a6fcb93d64709dd57058eaf84141))
* **config:** Fixed config save error on Windows and Linux ([d251016](https://github.com/fizzi01/Perpetua/commit/d25101647d55f11ac81352d045efaa834832ec82))
* **gui:** auto-discovery mode info popup not showing correctly ([#50](https://github.com/fizzi01/Perpetua/issues/50)) ([c3b6740](https://github.com/fizzi01/Perpetua/commit/c3b6740944e126e6cd9c3d678fe169ab2b0cf938))
* **gui:** avoid spamming force stop events with multiple presses ([9823faa](https://github.com/fizzi01/Perpetua/commit/9823faacab10a6fcb93d64709dd57058eaf84141))
* improve GUI performance on macos ([eafba5b](https://github.com/fizzi01/Perpetua/commit/eafba5bbe19021dc0f1a428d95924fdce1ff5a59))
* **mouse:** Improve double-click emulation ([d251016](https://github.com/fizzi01/Perpetua/commit/d25101647d55f11ac81352d045efaa834832ec82))

## [1.2.4](https://github.com/fizzi01/Perpetua/compare/v1.2.3...v1.2.4) (2026-02-18)


### Bug Fixes

* **daemon:** ensure graceful shutdown ([f1ff45f](https://github.com/fizzi01/Perpetua/commit/f1ff45f55df141589e861ffaf74171dd80486b43))
* **gui:** adjust splash-screen border-radius on Windows ([#45](https://github.com/fizzi01/Perpetua/issues/45)) ([9a1e8e1](https://github.com/fizzi01/Perpetua/commit/9a1e8e127ff8270a00c34f5e7d50b27777f26c6f))
* improve cpu usage and streams performance ([#43](https://github.com/fizzi01/Perpetua/issues/43)) ([f1ff45f](https://github.com/fizzi01/Perpetua/commit/f1ff45f55df141589e861ffaf74171dd80486b43))
* **launcher:** fix launcher behaviour ([f1ff45f](https://github.com/fizzi01/Perpetua/commit/f1ff45f55df141589e861ffaf74171dd80486b43))

## [1.2.3](https://github.com/fizzi01/Perpetua/compare/v1.2.2...v1.2.3) (2026-02-17)


### Bug Fixes

* **build:** update macOS app bundle configuration ([781d097](https://github.com/fizzi01/Perpetua/commit/781d097e81895d3688894d5fae283326fb3fa653))
* improve macOS dock icon suppression logic ([781d097](https://github.com/fizzi01/Perpetua/commit/781d097e81895d3688894d5fae283326fb3fa653))
* unhandle mdns service exception, general errors logging improvements ([#39](https://github.com/fizzi01/Perpetua/issues/39)) ([781d097](https://github.com/fizzi01/Perpetua/commit/781d097e81895d3688894d5fae283326fb3fa653))

## [1.2.2](https://github.com/fizzi01/Perpetua/compare/v1.2.1...v1.2.2) (2026-02-16)


### Bug Fixes

* extend mouse control to higher polling rates and improve double-click  ([#34](https://github.com/fizzi01/Perpetua/issues/34)) ([06029f3](https://github.com/fizzi01/Perpetua/commit/06029f35ea310b9947dab0cfcde0b9e8400ac7fb))
* **gui:** removed client hostname info when connected ([#38](https://github.com/fizzi01/Perpetua/issues/38)) ([517be4d](https://github.com/fizzi01/Perpetua/commit/517be4da2e685d2d36ce174b1571c5c777d063eb))
* **mouse:** fix sticky cursor when dragging ([3462bc9](https://github.com/fizzi01/Perpetua/commit/3462bc9c38cef6922fa4c98eb29dee733161c232))
* **network:** improve performance in receive and send data ([#37](https://github.com/fizzi01/Perpetua/issues/37)) ([3462bc9](https://github.com/fizzi01/Perpetua/commit/3462bc9c38cef6922fa4c98eb29dee733161c232))

## [1.2.1](https://github.com/fizzi01/Perpetua/compare/v1.2.0...v1.2.1) (2026-02-13)


### Bug Fixes

* **mouse:** optimize and fix edge crossing ([#31](https://github.com/fizzi01/Perpetua/issues/31)) ([c776755](https://github.com/fizzi01/Perpetua/commit/c7767550d5b584f1995e6a3c4277603109ce754d))
* overall mouse control performance boost and improvement ([#33](https://github.com/fizzi01/Perpetua/issues/33)) ([17e544c](https://github.com/fizzi01/Perpetua/commit/17e544cc460bd5d8ee0548b8513f4aaf3b7bbd32))


### Documentation

* update README for clarity and improved structure ([0f07e6c](https://github.com/fizzi01/Perpetua/commit/0f07e6c8faf74332aa49440d03991ac3163dd511))

## [1.2.0](https://github.com/fizzi01/Perpetua/compare/v1.1.0...v1.2.0) (2026-02-12)


### ⚠ BREAKING CHANGES

* **mouse:** clamp cursor position to screen bounds ([#25](https://github.com/fizzi01/Perpetua/issues/25))
* **keyboard:** keyboard events not being suppressed when caps lock is active on macOS

### Features

* **build:** avoid useless file copies ([0f3a417](https://github.com/fizzi01/Perpetua/commit/0f3a417db88c76f483b0ffce5faf1ec802bec6b8))
* **connection:** implement exponential backoff for client connection retry logic ([bd3665a](https://github.com/fizzi01/Perpetua/commit/bd3665aba60b2843aaa42d3198aea6ecdc286c1b))
* **cursor:** add previous app focus restore on Windows ([bd3665a](https://github.com/fizzi01/Perpetua/commit/bd3665aba60b2843aaa42d3198aea6ecdc286c1b))
* **gui:** enhance tray icon behaviour ([#28](https://github.com/fizzi01/Perpetua/issues/28)) ([73cd4ec](https://github.com/fizzi01/Perpetua/commit/73cd4ec00fc51458152790d651e20428ac40e153))
* **gui:** switch tray icon based on connection state on macOS ([30f5993](https://github.com/fizzi01/Perpetua/commit/30f599365cbacc315e8d9a8d93c54182eedd02bc))
* **keyboard:** enhance Caps Lock state management ([30f5993](https://github.com/fizzi01/Perpetua/commit/30f599365cbacc315e8d9a8d93c54182eedd02bc))
* **mouse:** improve screen crossing by preventing crossing while mouse main buttons are being pressed ([#23](https://github.com/fizzi01/Perpetua/issues/23)) ([7ef2cfa](https://github.com/fizzi01/Perpetua/commit/7ef2cfae9849223fb919fc1de60e2ef955bef2ce))
* optimize startup performance and behaviour ([#27](https://github.com/fizzi01/Perpetua/issues/27)) ([1d9b0c1](https://github.com/fizzi01/Perpetua/commit/1d9b0c1e8ba7ccc5f4446aafc3b263c23f3860c5))
* replace json with msgspec for improved performance in encoding/decoding ([1c6d249](https://github.com/fizzi01/Perpetua/commit/1c6d249e1e63b37ab3efc00292ed317e956c675f))


### Bug Fixes

* **connection:** cap error count to prevent overflow ([bd3665a](https://github.com/fizzi01/Perpetua/commit/bd3665aba60b2843aaa42d3198aea6ecdc286c1b))
* **cursor:** old focused app not getting focus again on macOS ([bd3665a](https://github.com/fizzi01/Perpetua/commit/bd3665aba60b2843aaa42d3198aea6ecdc286c1b))
* **gui:** update color variables for improved theming in log viewer ([13c28b8](https://github.com/fizzi01/Perpetua/commit/13c28b81adeb7bbf0ccd4d15945bcd019aa01050))
* inconsistent dock behaviour on macOS ([c8f2f02](https://github.com/fizzi01/Perpetua/commit/c8f2f02a9c7c080a6bcaddc136e0f856f77cbf5e))
* **keyboard:** keyboard events not being suppressed when caps lock is active on macOS ([30f5993](https://github.com/fizzi01/Perpetua/commit/30f599365cbacc315e8d9a8d93c54182eedd02bc))
* **mouse:** clamp cursor position to screen bounds ([#25](https://github.com/fizzi01/Perpetua/issues/25)) ([30f5993](https://github.com/fizzi01/Perpetua/commit/30f599365cbacc315e8d9a8d93c54182eedd02bc))
* **mouse:** hide dock icon on macOS when accessing controller position ([ad5b6e2](https://github.com/fizzi01/Perpetua/commit/ad5b6e2ca1d8d2135ec7427d393270c895b6bedc))
* **mouse:** reduce double-click detection time from 200 to 100ms ([7ef2cfa](https://github.com/fizzi01/Perpetua/commit/7ef2cfae9849223fb919fc1de60e2ef955bef2ce))
* **network:** missing target reassignment when sending messages in multicast ([1c6d249](https://github.com/fizzi01/Perpetua/commit/1c6d249e1e63b37ab3efc00292ed317e956c675f))
* some imports led to missing modules in nuitka build, wrong method signature ([49b5c4b](https://github.com/fizzi01/Perpetua/commit/49b5c4b71cd408224156e8b1f75733ca0128da88))
* suppress extra dock icon when client connects on macOS ([30f5993](https://github.com/fizzi01/Perpetua/commit/30f599365cbacc315e8d9a8d93c54182eedd02bc))
* unwanted previous app focus on start/stop server (macOS) ([9949ab1](https://github.com/fizzi01/Perpetua/commit/9949ab10b5cd8c040b11398543cb3246b68c189b))


### Documentation

* add tip in Usage section ([da90fb2](https://github.com/fizzi01/Perpetua/commit/da90fb2b84f875e26dfbc3ea0bbf79b58b33d013))


### Miscellaneous Chores

* release 1.2.0 ([b937304](https://github.com/fizzi01/Perpetua/commit/b937304297f5ae75ce8b3a70aca36aac4ecc85b8))

## [1.1.0](https://github.com/fizzi01/Perpetua/compare/v1.0.0...v1.1.0) (2026-02-05)


### Features

* **gui:** improve startup handling, implement splash screen, improve internal app state ([#16](https://github.com/fizzi01/Perpetua/issues/16)) ([ff2abfb](https://github.com/fizzi01/Perpetua/commit/ff2abfb7b0773c8db6a070e8de6e7bf463b89060))


### Bug Fixes

* **daemon:** unhandled exceptions when closing streams ([ff2abfb](https://github.com/fizzi01/Perpetua/commit/ff2abfb7b0773c8db6a070e8de6e7bf463b89060))
* **gui:** adjust window shadow and titlebar z-index on Windows, fixed splash-screen border radius ([#19](https://github.com/fizzi01/Perpetua/issues/19)) ([0791728](https://github.com/fizzi01/Perpetua/commit/0791728f4ae4abe15f2834cc6dd7f6f87effce75))
* **gui:** fix app unsync on startup ([ff2abfb](https://github.com/fizzi01/Perpetua/commit/ff2abfb7b0773c8db6a070e8de6e7bf463b89060))
* **gui:** fix critical error by ensuring shutdown command dispatch only if connected ([30f32b1](https://github.com/fizzi01/Perpetua/commit/30f32b17c74a40408b69785e7ca3f8abd6451361))
* **gui:** fixed wrong rendering on macos and missing rounded corners on splashscreen ([#18](https://github.com/fizzi01/Perpetua/issues/18)) ([30f32b1](https://github.com/fizzi01/Perpetua/commit/30f32b17c74a40408b69785e7ca3f8abd6451361))
* **protocol:** handle None case for single chunk in reconstruct_from_chunks ([#20](https://github.com/fizzi01/Perpetua/issues/20)) ([05f72b4](https://github.com/fizzi01/Perpetua/commit/05f72b40b480aa1d4e3e0ea9432e646173f8d3f8))


### Documentation

* add development setup instructions, minor changes ([868d38e](https://github.com/fizzi01/Perpetua/commit/868d38eaa81d517c9ceb488a5f671973a5527c9b))
* fix formatting of note in development instructions ([42801fd](https://github.com/fizzi01/Perpetua/commit/42801fd1f920bca0e9653d4d842f254909a8c607))
