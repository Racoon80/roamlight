//  The app. It can do four things: sign in, look at photographs, share and
//  upload. Everything else is the server's work -- the app computes nothing, it
//  shows and it passes on.

import SwiftUI

@main
struct RoamlightApp: App {
    @StateObject private var state = AppState()
    // ⚠ The delegate exists for one reason: only UIKit is handed the address
    //   Apple mints for this phone. SwiftUI has no way to receive it.
    @UIApplicationDelegateAdaptor(Notices.self) private var notices

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(state)
                .preferredColorScheme(.dark)      // the site is dark, so is the app
                .tint(Theme.safelight)
        }
    }
}

/// The colours out of `static/site.css`, so that the app and the site look
/// ausgesinn.
enum Theme {
    static let ground     = Color(red: 0.102, green: 0.094, blue: 0.082)   // #1a1815
    static let groundWarm = Color(red: 0.133, green: 0.122, blue: 0.106)   // #221f1b
    static let ink        = Color(red: 0.949, green: 0.929, blue: 0.894)   // #f2ede4
    static let inkSoft    = Color(red: 0.773, green: 0.737, blue: 0.682)   // #c5bcae
    static let inkMute    = Color(red: 0.553, green: 0.514, blue: 0.459)   // #8d8375
    static let safelight  = Color(red: 0.416, green: 0.663, blue: 0.878)   // #6aa9e0
}

@MainActor
final class AppState: ObservableObject {
    @Published var token: String? = Keychain.load()
    @Published var me: Me?
    @Published var error: String?
    @Published var checking = false
    /// The album a tapped notice asked for -- `<year>/<country>/<event>`.
    @Published var openAlbum: String?

    var api: API { API(token: token) }
    var connected: Bool { token != nil }

    /// After pairing, and at every start: who am I and what may I.
    /// ⚠ A 401 means the device was revoked on the site -- then do NOT keep
    ///   retrying: throw the token away and ask again. Otherwise the app hangs
    ///   in a loop of errors nobody can lift.
    func refresh() async {
        guard token != nil else { return }
        checking = true
        defer { checking = false }
        do {
            me = try await api.me()
            error = nil
        } catch APIError.http(401, _) {
            signOut()
        } catch {
            self.error = error.localizedDescription
        }
    }

    func connect(code: String, name: String, site: String) async -> Bool {
        do {
            let p = try await API.pair(code: code, name: name, site: site)
            Keychain.save(p.token)
            token = p.token
            await refresh()
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }

    func signIn(site: String, user: String, password: String,
                name: String, done: @escaping (Bool) -> Void = { _ in }) {
        Task {
            checking = true
            do {
                let p = try await API.signIn(site: site.trimmingCharacters(in: .whitespaces),
                                             user: user, password: password, name: name)
                Keychain.save(p.token)
                token = p.token
                error = nil
                checking = false
                await refresh()
                done(true)
            } catch {
                self.error = error.localizedDescription
                checking = false
                done(false)
            }
        }
    }

    func signOut() {
        // ⚠ Before the token goes: tell the site to stop sending here.
        //   Afterwards there is nothing left to say it with.
        Notices.forget()
        Keychain.delete()
        token = nil
        me = nil
        ImageStore.shared.clear()
    }
}

struct RootView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        Group {
            if state.connected {
                MainView()
            } else {
                PairView()
            }
        }
        .task {
            await state.refresh()
            // ⚠ Only once there IS a connection. Asking before that means
            //   asking somebody who has not yet seen a single photograph.
            if state.connected {
                NoticeStore.api = { state.api }
                NoticeStore.open = { key in state.openAlbum = key }
                // A notice tapped on a cold start arrived before there was
                // anywhere to send it.
                if let waiting = NoticeStore.waiting {
                    NoticeStore.waiting = nil
                    state.openAlbum = waiting
                }
                Notices.askAndRegister()
            }
        }
    }
}

struct MainView: View {
    @EnvironmentObject var state: AppState
    @State private var tab = 0
    @State private var path = NavigationPath()

    var body: some View {
        TabView(selection: $tab) {
            NavigationStack(path: $path) { AlbumsView() }
                .tabItem { Label("Albums", systemImage: "square.grid.2x2") }
                .tag(0)

            NavigationStack { SearchView() }
                .tabItem { Label("Search", systemImage: "magnifyingglass") }
                .tag(1)

            if state.me?.may.upload == true {
                NavigationStack { UploadView() }
                    .tabItem { Label("New album", systemImage: "plus.rectangle.on.folder") }
                    .tag(2)
            }

            NavigationStack { SettingsView() }
                .tabItem { Label("Device", systemImage: "iphone") }
                .tag(3)
        }
        // ⚠ A tapped notice lands here. The album is named by its key
        //   (`<year>/<country>/<event>`), which is all the list needs; the
        //   title and the count come from the album page itself.
        .onChange(of: state.openAlbum) { _, key in
            guard let key, !key.isEmpty else { return }
            let parts = key.split(separator: "/", maxSplits: 2).map(String.init)
            guard parts.count == 3 else { return }
            tab = 0
            path = NavigationPath()          // start from the list, not on top of it
            path.append(Album(year: parts[0], country: parts[1], event: parts[2],
                              title: "", n: 0, cover: nil, coverRev: nil, latest: nil))
            state.openAlbum = nil
        }
    }
}
