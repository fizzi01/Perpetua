# Changelog

## [1.2.0](https://github.com/fizzi01/Perpetua/compare/v1.1.0...v1.2.0) (2026-02-07)


### Features

* **connection:** implement exponential backoff for client connection retry logic ([bd3665a](https://github.com/fizzi01/Perpetua/commit/bd3665aba60b2843aaa42d3198aea6ecdc286c1b))
* **cursor:** add previous app focus restore on Windows ([bd3665a](https://github.com/fizzi01/Perpetua/commit/bd3665aba60b2843aaa42d3198aea6ecdc286c1b))
* **mouse:** improve screen crossing by preventing crossing while mouse main buttons are being pressed ([#23](https://github.com/fizzi01/Perpetua/issues/23)) ([7ef2cfa](https://github.com/fizzi01/Perpetua/commit/7ef2cfae9849223fb919fc1de60e2ef955bef2ce))
* replace json with msgspec for improved performance in encoding/decoding ([1c6d249](https://github.com/fizzi01/Perpetua/commit/1c6d249e1e63b37ab3efc00292ed317e956c675f))


### Bug Fixes

* **connection:** cap error count to prevent overflow ([bd3665a](https://github.com/fizzi01/Perpetua/commit/bd3665aba60b2843aaa42d3198aea6ecdc286c1b))
* **cursor:** old focused app not getting focus again on macOS ([bd3665a](https://github.com/fizzi01/Perpetua/commit/bd3665aba60b2843aaa42d3198aea6ecdc286c1b))
* **mouse:** reduce double-click detection time from 200 to 100ms ([7ef2cfa](https://github.com/fizzi01/Perpetua/commit/7ef2cfae9849223fb919fc1de60e2ef955bef2ce))
* **network:** missing target reassignment when sending messages in multicast ([1c6d249](https://github.com/fizzi01/Perpetua/commit/1c6d249e1e63b37ab3efc00292ed317e956c675f))

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
