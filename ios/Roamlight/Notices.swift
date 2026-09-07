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

final class Notices: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    static let shared = Notices()

    /// Set by the app once it knows how to talk to the site.
    var api: (() -> API)?

    private var lastToken: String?

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    /// Ask, and register if allowed. Doing it again is harmless -- iOS answers
    /// out of what was already decided and does not ask twice.
    func askAndRegister() {
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
        lastToken = hex
        guard let api = api?() else { return }
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
    func forget() {
        guard let token = lastToken, let api = api?() else { return }
        Task { try? await api.forgetNotices(token: token) }
        lastToken = nil
    }

    /// A notice while the app is open is shown anyway -- otherwise somebody
    /// looking at one album never hears that another one grew.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }
}
