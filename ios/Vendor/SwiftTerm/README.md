# SwiftTerm: attributed iOS source adaptation

This is third-party SwiftTerm, licensed under the complete upstream MIT notice in `LICENSE`.

- Upstream: <https://github.com/migueldeicaza/SwiftTerm>
- Original revision: `a183c15b52858530a17de57dcc72b93eeebc9857` (v1.99.2).
- Downstream adaptation: `command-helm-ios-no-local-media-fix1`.
- This snapshot is a bounded iOS library distribution, not an upstream release or a new fork.

`SOURCE-MANIFEST.json` records the original source path, original SHA-256 and delivered SHA-256 for every copied source/metadata file. Run the offline verifier from the companion repository:

```sh
python3 ios/Vendor/SwiftTerm/verify-source.py
```

It verifies current bytes and reverses `DOWNSTREAM.patch` in a disposable copy to prove that the result matches every original hash. The source was copied from a verified, clean checkout of the exact revision above. No package cache or global tool setting was changed.

## Bounded selection and packaging

`Sources/SwiftTerm` contains the upstream Swift core and Apple library source files. Documentation catalogs are omitted. Original platform-guarded Mac library sources are retained because `MacAccessibilityService.swift` also implements the iOS accessibility service. The unbuilt Metal shader source file is also omitted, matching the original manifest's exclusion. No examples, executables, binaries, build products, `.git` directory or downloaded transitive packages are included.

The small downstream `Package.swift` builds the iOS library with Swift 6.2 and iOS 14+ declarations. It has no remote packages, executable targets or build plugins. Its original manifest is preserved at `Upstream/Package.swift` as provenance, not executed package configuration. CLI, documentation and non-Apple portable PNG/LZ77 targets are outside this iOS library selection. Those original targets' package requirements are not runtime dependencies of this app.

## Explicit downstream patch

`DOWNSTREAM.patch` records all changes to copied upstream code and the package manifest:

1. `Sources/SwiftTerm/KittyGraphics.swift` returns an unsupported-medium response on iOS for local file, temporary-file and shared-memory transmissions (`t=f`, `t=t`, `t=s`). Their dispatch call sites, local path/file readers, shared-memory readers and `shm_open` declaration are conditionally compiled out on iOS. The original code remains unchanged for other conditional platforms; this package's supported consumer is iOS. Direct byte payloads, the terminal renderer, parser, input and resizing behavior are retained. This restriction also applies if a future host accidentally requests an all-local-media policy.
2. The host build plugin is replaced with checked-in deterministic generated build-info and terminfo Swift files. The original generator, plugin, terminfo input and original generated outputs are retained in `Upstream/` for inspection. `Sources/SwiftTerm/Generated/SwiftTermTerminfo.swift` is byte-identical to its original generated output. The delivered build-info file preserves the exact original upstream revision/tag, explicitly labels this downstream adaptation and marks `hasUncommittedChanges = true`, producing the upstream's `-modified` version suffix. It never derives SwiftTerm identity from the enclosing Command Helm repository.
3. The manifest is reduced to the iOS library product described above. No automatic network resolution remains.

The app separately disables the entire Kitty graphics protocol and denies remote clipboard read/write or unsolicited URL opening. This source adaptation removes unused filesystem APIs from the iOS executable; it does not replace those existing privacy controls or introduce an inaccurate privacy-manifest reason.

## Reproduce generated-source provenance

Use a clean upstream checkout whose HEAD is exactly the revision above, not the enclosing companion checkout. Compile the preserved generator into an external temporary/cache directory:

```sh
swiftc ios/Vendor/SwiftTerm/Upstream/BuildInfoGenerator/*.swift -o /absolute/cache/SwiftTermBuildInfoGenerator
/absolute/cache/SwiftTermBuildInfoGenerator /absolute/path/to/verified-clean-upstream /absolute/cache/SwiftTermBuildInfo.swift /absolute/cache/SwiftTermTerminfo.swift
cmp ios/Vendor/SwiftTerm/Upstream/Generated/SwiftTermBuildInfo.swift /absolute/cache/SwiftTermBuildInfo.swift
cmp ios/Vendor/SwiftTerm/Upstream/Generated/SwiftTermTerminfo.swift /absolute/cache/SwiftTermTerminfo.swift
```

Those comparisons were run when creating this adaptation. The modified delivered build-info file is intentionally different from the original generated output, and that difference is included in the patch and file hashes.
