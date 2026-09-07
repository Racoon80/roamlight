//  Signing in: scan the QR code from the `/app` page.
//
//  ⚠ The QR code holds no token but a pairing code (five minutes, one use) --
//    an der Form `https://<site>/app/pair#c=<code>`. D'App zitt souwuel
//    both the site's address and the code come out of it: nobody types a URL.

import AVFoundation
import SwiftUI
import UIKit

struct PairView: View {
    @EnvironmentObject var state: AppState
    @State private var scanning = false
    @State private var code = ""
    // ⚠ Empty, not a guess: the QR code brings the address along. Somebody
    //   typing it by hand types their own.
    @State private var site = ""
    @State private var user = ""
    @State private var password = ""
    @State private var busy = false

    private var deviceName: String { UIDevice.current.name }

    var body: some View {
        ZStack {
            Theme.ground.ignoresSafeArea()
            VStack(spacing: 22) {
                Spacer()
                Text("Roamlight")
                    .font(.system(size: 34, weight: .semibold, design: .serif))
                    .foregroundStyle(Theme.ink)
                Text("Open the site on a computer, go to **Phone & tablet**, and press *Show the code*.")
                    .font(.callout)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(Theme.inkSoft)
                    .padding(.horizontal, 32)

                Button {
                    scanning = true
                } label: {
                    Label("Scan the code", systemImage: "qrcode.viewfinder")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                }
                .buttonStyle(.borderedProminent)
                .padding(.horizontal, 32)

                // ⚠ The other way in, and it is not a fallback. A pairing code
                //   needs a second machine: the site open on a computer, and
                //   five minutes. Somebody holding only a phone had no way in
                //   at all -- which is also what an App Store reviewer is.
                DisclosureGroup("Sign in with a password") {
                    VStack(spacing: 10) {
                        TextField("Site", text: $site)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                        TextField("Name", text: $user)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        SecureField("Password", text: $password)
                        Button("Sign in") {
                            state.signIn(site: site, user: user, password: password,
                                         name: deviceName)
                        }
                        .disabled(site.isEmpty || user.isEmpty || password.isEmpty
                                  || state.checking)
                    }
                    .textFieldStyle(.roundedBorder)
                    .padding(.top, 8)
                }
                .foregroundStyle(Theme.inkSoft)
                .padding(.horizontal, 32)

                DisclosureGroup("Or type the code") {
                    VStack(spacing: 10) {
                        TextField("Site", text: $site)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        TextField("Code", text: $code)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        Button("Connect") { Task { await connect(code: code, site: site) } }
                            .disabled(code.isEmpty || busy)
                    }
                    .textFieldStyle(.roundedBorder)
                    .padding(.top, 8)
                }
                .foregroundStyle(Theme.inkSoft)
                .padding(.horizontal, 32)

                if busy { ProgressView().tint(Theme.safelight) }
                if let e = state.error {
                    Text(e).font(.footnote).foregroundStyle(.red)
                        .multilineTextAlignment(.center).padding(.horizontal, 32)
                }
                Spacer()
                Text("This device will see exactly what you see on the site.")
                    .font(.caption2).foregroundStyle(Theme.inkMute)
                    .padding(.bottom, 12)
            }
        }
        .sheet(isPresented: $scanning) {
            ScannerSheet { found in
                scanning = false
                let (c, s) = Self.parse(found)
                Task { await connect(code: c, site: s ?? site) }
            }
        }
    }

    private func connect(code: String, site: String) async {
        guard !code.isEmpty else { return }
        busy = true
        defer { busy = false }
        _ = await state.connect(code: code, name: deviceName, site: site)
    }

    /// `https://photos.example.com/app/pair#c=ABC` -> ("ABC", "https://photos.example.com").
    /// A bare code (typed by hand) comes back unchanged.
    static func parse(_ text: String) -> (String, String?) {
        guard let u = URLComponents(string: text), let host = u.host else { return (text, nil) }
        var code = ""
        if let frag = u.fragment {
            for part in frag.split(separator: "&") where part.hasPrefix("c=") {
                code = String(part.dropFirst(2)).removingPercentEncoding ?? String(part.dropFirst(2))
            }
        }
        let scheme = u.scheme ?? "https"
        let port = u.port.map { ":\($0)" } ?? ""
        return (code, "\(scheme)://\(host)\(port)")
    }
}

// MARK: - De Scanner

/// ⚠ SwiftUI huet (nach) kee QR-Scanner. Also AVFoundation an engem
///   UIViewController -- and the session runs on a thread of its own, because
///   `startRunning()` would otherwise block the screen.
struct ScannerSheet: View {
    let found: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScannerView(found: found)
                .ignoresSafeArea()
                .overlay(alignment: .bottom) {
                    Text("Hold the camera at the square on the screen")
                        .font(.footnote)
                        .padding(10)
                        .background(.black.opacity(0.6), in: Capsule())
                        .foregroundStyle(.white)
                        .padding(.bottom, 40)
                }
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { dismiss() }
                    }
                }
        }
    }
}

struct ScannerView: UIViewControllerRepresentable {
    let found: (String) -> Void

    func makeUIViewController(context: Context) -> ScannerController {
        let c = ScannerController()
        c.onFound = found
        return c
    }

    func updateUIViewController(_ c: ScannerController, context: Context) {}
}

final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var onFound: ((String) -> Void)?
    private let session = AVCaptureSession()
    private var layer: AVCaptureVideoPreviewLayer?
    private var done = false

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        guard let device = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else { return }
        session.addInput(input)

        let out = AVCaptureMetadataOutput()
        guard session.canAddOutput(out) else { return }
        session.addOutput(out)
        out.setMetadataObjectsDelegate(self, queue: .main)
        out.metadataObjectTypes = [.qr]

        let l = AVCaptureVideoPreviewLayer(session: session)
        l.videoGravity = .resizeAspectFill
        l.frame = view.bounds
        view.layer.addSublayer(l)
        layer = l
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        layer?.frame = view.bounds
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        // ⚠ Net um Haaptfued: `startRunning()` brauch e puer honnert
        //   milliseconds and would otherwise hang the view.
        let s = session
        Task.detached { s.startRunning() }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        let s = session
        Task.detached { s.stopRunning() }
    }

    func metadataOutput(_ output: AVCaptureMetadataOutput,
                        didOutput objects: [AVMetadataObject],
                        from connection: AVCaptureConnection) {
        guard !done,
              let o = objects.first as? AVMetadataMachineReadableCodeObject,
              let text = o.stringValue else { return }
        done = true                       // ⚠ only ONCE -- otherwise it comes
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        onFound?(text)                    //   Code Dosende Mol duerch
    }
}
