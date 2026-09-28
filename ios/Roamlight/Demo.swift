//  DEBUG builds only: let a simulator be driven from the command line, so the
//  screens can be looked at against a local test site without anybody tapping.
//
//      SIMCTL_CHILD_ROAMLIGHT_DEMO_SITE=http://127.0.0.1:8765 \
//      SIMCTL_CHILD_ROAMLIGHT_DEMO_CODE=<pairing code> \
//      SIMCTL_CHILD_ROAMLIGHT_DEMO_OPEN=2017/Luxembourg/Kanddaaf%20Jana \
//        xcrun simctl launch booted lu.racoon.roamlight
//
//  ⚠ In the ENVIRONMENT of `simctl`, in front of it -- simctl hands every
//    `SIMCTL_CHILD_*` variable on without the prefix. Written after the bundle
//    id they are only arguments, and nothing happens.
//
//  ⚠ None of this is in a Release build (TestFlight, App Store): the whole
//    file is `#if DEBUG`, and so is every call into it.

#if DEBUG
import Foundation

enum Demo {
    static func env(_ key: String) -> String? {
        ProcessInfo.processInfo.environment[key].flatMap { $0.isEmpty ? nil : $0 }
    }

    @MainActor
    static func pairIfAsked(_ state: AppState) async {
        guard !state.connected, let site = env("ROAMLIGHT_DEMO_SITE"),
              let code = env("ROAMLIGHT_DEMO_CODE") else { return }
        _ = await state.connect(code: code, name: "Simulator", site: site)
    }

    @MainActor
    static func openIfAsked(_ state: AppState) async {
        guard let key = env("ROAMLIGHT_DEMO_OPEN")?.removingPercentEncoding else { return }
        try? await Task.sleep(for: .seconds(1.5))
        state.openAlbum = key
    }
}
#endif
