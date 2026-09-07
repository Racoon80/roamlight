//  Bescheed op den Telefon.
//
//  ⚠ The app asks Apple for an address, and hands that address to the family's
//    own site. Nothing else. The site decides WHAT is worth a notice and, above
//    all, HOW OFTEN -- a hundred photographs are one line, not a hundred
//    buzzes. That rule lives on the server (app/notify.py), because it has to
//    hold for every phone at once.
//
//  ⚠ Permission is asked ONCE and only when there is something to be told
//    about: after the device is paired. Asking on the very first screen, before
//    anybody has seen a photograph, is how an app gets refused for good.

import SwiftUI
import UIKit
import UserNotifications

/// ⚠ EVERYTHING HERE IS STATIC, and that is not laziness.
///
///   `@UIApplicationDelegateAdaptor(Notices.self)` builds its OWN instance --
///   it does not use `Notices.shared`. Hanging the connection to the site on
///   the shared object meant that Apple handed the address to one object while
///   the way to send it lived on another, and the registration quietly did
///   nothing at all: no call to the site, no line in its log, and a phone that
///   simply never appeared on the list.
enum NoticeStore {
    /// Set by the app once it knows how to talk to the site.
    static var api: (() -> API)?
    static var lastToken: String?

    /// ⚠ Where a tapped notice should take you. Set by the app; called from the
    ///   delegate. A notice that only opens the app is barely a notice: it says
    ///   something happened and then leaves you to find it.
    static var open: ((String) -> Void)?

    /// ⚠ A notice can arrive before the app is ready to go anywhere -- a cold
    ///   start opens on the tap, and the album list is not loaded yet. So it is
    ///   kept, and picked up once there is something to pick it up.
    static var waiting: String?
}

final class Notices: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    static let shared = Notices()

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    /// Ask, and register if allowed. Doing it again is harmless -- iOS answers
    /// out of what was already decided and does not ask twice.
    static func askAndRegister() {
        UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
                guard granted else { return }
                DispatchQueue.main.async {
                    UIApplication.shared.registerForRemoteNotifications()
                }
            }
    }

    func application(_ application: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken data: Data) {
        // ⚠ Apple hands over raw bytes; the server wants the hexadecimal that
        //   the push service expects in the address. Getting this wrong gives
        //   `BadDeviceToken`, which says nothing about what is actually wrong.
        let hex = data.map { String(format: "%02x", $0) }.joined()
        NoticeStore.lastToken = hex
        guard let api = NoticeStore.api?() else {
            NSLog("Roamlight: got a notice address before the site was known")
            return
        }
        Task {
            try? await api.registerForNotices(token: hex, name: UIDevice.current.name)
        }
    }

    func application(_ application: UIApplication,
                     didFailToRegisterForRemoteNotificationsWithError error: Error) {
        // Nothing to do: no address, no notices. The app works either way.
        NSLog("Roamlight: no notice address — %@", error.localizedDescription)
    }

    /// Taking this device off: the site should stop sending here.
    static func forget() {
        guard let token = NoticeStore.lastToken, let api = NoticeStore.api?() else { return }
        Task { try? await api.forgetNotices(token: token) }
        NoticeStore.lastToken = nil
    }

    /// A notice while the app is open is shown anyway -- otherwise somebody
    /// looking at one album never hears that another one grew.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    /// Somebody tapped it. Take them to the album it was about.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse) async {
        let info = response.notification.request.content.userInfo
        guard let album = info["album"] as? String, !album.isEmpty else { return }
        await MainActor.run {
            if let open = NoticeStore.open {
                open(album)
            } else {
                NoticeStore.waiting = album
            }
        }
    }
}
