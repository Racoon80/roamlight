//  Your originals, in your own cloud.
//
//  ⚠ What this is for: a photograph uploaded by somebody who is not an
//    administrator keeps no original on the site -- it is converted and the
//    upload thrown away. With a cloud connected (Nextcloud, on the site's
//    side app/cloud.py), every original is copied there first, in the same
//    folders as on the site, and the site lets go of its copy only once the
//    one in the cloud has been read back and matched.
//
//  ⚠ How connecting works: Nextcloud's Login Flow v2. The site asks the cloud
//    for a sign-in page; it opens here in a Safari sheet; the person signs in
//    and approves; meanwhile this view asks the site every two seconds
//    whether the cloud has handed over the password yet. The password goes
//    from the cloud straight to the site -- it never passes through the phone.

import SafariServices
import SwiftUI

struct CloudSection: View {
    @EnvironmentObject var state: AppState
    /// Loaded by the page around it. ⚠ Not by a `.task` in here: a view whose
    /// body is still empty is never put on screen, so its `.task` never ran
    /// and the section never appeared.
    @Binding var status: CloudStatus?
    /// The cloud's sign-in page while it is open. ⚠ Owned and PRESENTED by
    /// the page around this section: a `.sheet` hung on a row inside a `Form`
    /// was dismissed again the moment it appeared.
    @Binding var page: CloudPage?
    @State private var working = false
    @State private var message: String?
    @State private var askOff = false

    var body: some View {
        Group {
            if let s = status, s.available {
                Section {
                    if s.connected {
                        LabeledContent("Cloud", value: host(s.server))
                        LabeledContent("As", value: s.login ?? "—")
                        LabeledContent("Folder", value: s.folder ?? "—")
                        LabeledContent("Copied", value: "\(s.copied ?? 0)")
                        if let w = s.waiting, w > 0 {
                            LabeledContent("Waiting", value: "\(w)")
                            Button("Try again now") { Task { await retry() } }
                                .disabled(working)
                        }
                        if let e = s.lastError, !e.isEmpty {
                            Text(e).font(.caption).foregroundStyle(.red)
                        }
                        Button("Disconnect", role: .destructive) { askOff = true }
                            .disabled(working)
                    } else {
                        Button {
                            Task { await connect() }
                        } label: {
                            HStack {
                                Label("Connect my cloud", systemImage: "icloud.and.arrow.up")
                                if working { Spacer(); ProgressView() }
                            }
                        }
                        .disabled(working)
                    }
                    if let message {
                        Text(message).font(.caption).foregroundStyle(Theme.inkSoft)
                    }
                } header: {
                    Text("Your originals")
                } footer: {
                    if s.connected {
                        Text("Every photograph you upload is copied in full to your cloud, "
                             + "in the same folders as on the site.")
                    } else {
                        Text("Without this the site keeps only a 4000-pixel copy of what you "
                             + "upload and throws the original away. Connect \(host(s.server)) "
                             + "and every original goes there first.")
                    }
                }
            }
        }
        .confirmationDialog("Stop copying your originals to your cloud?",
                            isPresented: $askOff, titleVisibility: .visible) {
            Button("Disconnect", role: .destructive) { Task { await disconnect() } }
            Button("Keep it", role: .cancel) {}
        } message: {
            Text("Your files in the cloud stay where they are. Anything not copied yet "
                 + "is discarded after conversion, as before.")
        }
    }

    private func host(_ s: String?) -> String {
        s.flatMap { URL(string: $0)?.host() } ?? "your cloud"
    }

    private func load() async {
        status = try? await state.api.cloudStatus()
    }

    private func connect() async {
        working = true; message = "Asking your cloud…"
        do {
            let start = try await state.api.cloudStart()
            guard let url = URL(string: start.loginURL) else { throw APIError.badResponse }
            page = CloudPage(url: url)
            message = "Sign in and approve — this page follows by itself."
            await waitForApproval()
        } catch {
            message = error.localizedDescription
            working = false
        }
    }

    /// Ask until the cloud has said yes, the sheet is gone, or twenty minutes
    /// have passed (Nextcloud's own limit for a Login Flow).
    private func waitForApproval() async {
        let until = Date().addingTimeInterval(20 * 60)
        while Date() < until {
            try? await Task.sleep(for: .seconds(2))
            guard let p = try? await state.api.cloudPoll() else { continue }
            if p.state == "connected" {
                page = nil
                message = "Connected."
                await load()
                working = false
                return
            }
            if p.state == "expired" || p.state == "none" {
                message = "That took too long — try again."
                working = false
                return
            }
            if page == nil { await finish(); return }   // closed by hand
        }
        page = nil
        working = false
    }

    /// The sheet was closed. Perhaps after approving, perhaps not: ask once.
    private func finish() async {
        if let p = try? await state.api.cloudPoll(), p.state == "connected" {
            message = "Connected."
        } else {
            message = "Not connected — the sign-in was closed before it was approved."
        }
        await load()
        working = false
    }

    private func retry() async {
        working = true
        if let s = try? await state.api.cloudRetry() { status = s }
        message = "On their way."
        working = false
    }

    private func disconnect() async {
        working = true
        do { status = try await state.api.cloudDisconnect(); message = nil }
        catch { message = error.localizedDescription }
        working = false
    }
}

/// The sign-in page being shown, as something a `.sheet(item:)` can follow.
struct CloudPage: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}

/// The cloud's sign-in page, in Safari's own sheet: its saved passwords and
/// the provider's single sign-on work there, and the page is plainly the
/// cloud's and not something this app drew.
struct SafariSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }
    func updateUIViewController(_ vc: SFSafariViewController, context: Context) {}
}
