//  Who am I, what may I, and how do I get out again.

import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var state: AppState
    @State private var askSignOut = false
    @State private var notices: NoticeState?

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

            Section("Notices") {
                // ⚠ Three different things go wrong here and they look alike
                //   from the outside: the site cannot send at all, this phone
                //   never handed over an address, or the address is there and
                //   nothing has been sent yet. Saying WHICH is the point.
                if let n = notices {
                    if !n.apns {
                        Text("The site is not set up to send notices.")
                            .foregroundStyle(Theme.inkMute)
                    } else if n.devices.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("This phone is not on the list.")
                            Text("Allow notifications for Roamlight in Settings. "
                                 + "⚠ The Simulator never gets a real address — "
                                 + "notices only arrive on a real phone.")
                                .font(.caption).foregroundStyle(Theme.inkMute)
                        }
                    } else {
                        ForEach(Array(n.devices.enumerated()), id: \.offset) { _, d in
                            LabeledContent(d.name.isEmpty ? d.kind : d.name) {
                                Text(d.lastOk.map { "last sent " + $0.prefix(10) }
                                     ?? "waiting")
                                    .foregroundStyle(Theme.inkMute)
                            }
                        }
                    }
                } else {
                    Text("—").foregroundStyle(Theme.inkMute)
                }
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
        .refreshable {
            await state.refresh()
            notices = try? await state.api.noticeState()
        }
        .task { notices = try? await state.api.noticeState() }
        .confirmationDialog("Take this device off?", isPresented: $askSignOut,
                            titleVisibility: .visible) {
            Button("Take it off", role: .destructive) { state.signOut() }
            Button("Keep it", role: .cancel) {}
        }
    }
}
