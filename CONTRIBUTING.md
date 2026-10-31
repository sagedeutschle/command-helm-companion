# Build and contribute

The iPhone app uses SwiftUI and XcodeGen. The bridge uses Python's standard library and the operating system's SSH and tmux tools.

## Bridge checks

```sh
python3 - <<'PY'
import os, subprocess, sys, tempfile
with tempfile.TemporaryDirectory(prefix="helm-tests-") as test_home:
    test_environment = dict(os.environ, HOME=test_home, PYTHONPATH="bridge")
    subprocess.run([sys.executable, "-W", "error", "-m", "unittest", "discover",
                    "-s", "bridge/tests"], env=test_environment, check=True)
PY
```

Run tests against disposable state only. Do not point tests at an installed user's state directory. Terminal integration tests require tmux and use a separate test socket.

## iOS checks

Install Xcode and XcodeGen, then run the `scripts/test-ios.py` helper with an available iOS simulator. It generates a temporary local TLS bridge and a test-only Swift fixture. The generated fixture belongs to the test targets and must never be included in the app target.

For a device build, generate the project:

```sh
xcodegen generate --spec ios/project.yml
```

Select your own signing team through `ios/project.yml` or an `xcodebuild` setting override. Keep DerivedData in a cache directory outside the repository and archive with `SWIFT_COMPILATION_MODE=incremental`. Do not edit the generated Xcode project as the source of truth.

The terminal uses an attributed local SwiftTerm source package with no remote dependencies or build plugins. XcodeGen restores the tracked empty `ios/Package.resolved` graph. The exact upstream revision, file hashes, generated-source provenance and downstream iOS patch are documented in [the vendor notice](ios/Vendor/SwiftTerm/README.md). Verify the delivered source and reverse patch offline with:

```sh
python3 ios/Vendor/SwiftTerm/verify-source.py
```

The downstream patch excludes unused local-file and shared-memory graphics helpers from iOS. SwiftTerm remains third-party code under its original MIT notice; the original Command Helm license does not replace that attribution.

## Changes

Use a focused pull request with the problem, resulting behavior and relevant validation. Preserve compatibility with saved task identities, pairing and older bridges. Never include credentials, real project output or personal network configuration in an issue, test fixture or pull request.
