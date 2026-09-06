//  Who am I, what may I, and how do I get out again.

import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var state: AppState
    @State private var askSignOut = false

    var body: some View {
        Form {
            Section("Signed in") {
                if let me = state.me {
                    LabeledContent("Name", value: me.user)
                    if !me.email.isEmpty { LabeledContent("Email", value: me.email) }
                    LabeledContent("May") {
                        Text([me.may.view ? "look" : nil,
                              me.may.upload ? "upload" : nil,
                              me.may.share ? "share" : nil,
                              me.may.admin ? "admin" : nil]
                            .compactMap { $0 }.joined(separator: " · "))
                    }
                } else if state.checking {
                    ProgressView()
                } else {
                    Text("Not reached").foregroundStyle(Theme.inkMute)
                }
                LabeledContent("Site", value: Site.url.host() ?? "—")
            }

            Section {
                Button("Take this device off", role: .destructive) { askSignOut = true }
            } footer: {
                // ⚠ Say honestly what the button does: it deletes the token
                //   HERE. The device stays on the list on the site until it is
                //   revoked there -- and that is the place that counts once the
                //   phone is gone.
                Text("This forgets the token on this phone. If the phone is lost, take it off the list on the site instead — that works even without the phone.")
            }
        }
        .navigationTitle("Device")
        .scrollContentBackground(.hidden)
        .background(Theme.ground)
        .refreshable { await state.refresh() }
        .confirmationDialog("Take this device off?", isPresented: $askSignOut,
                            titleVisibility: .visible) {
            Button("Take it off", role: .destructive) { state.signOut() }
            Button("Keep it", role: .cancel) {}
        }
    }
}
