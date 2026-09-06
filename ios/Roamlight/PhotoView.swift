//  One photograph, large -- and the link for sharing an album.

import SwiftUI

struct PhotoView: View {
    let photos: [Photo]
    let start: Photo

    @EnvironmentObject var state: AppState
    @State private var current: Int = 0

    var body: some View {
        TabView(selection: $current) {
            ForEach(Array(photos.enumerated()), id: \.offset) { i, p in
                ZoomableImage(id: p.id)
                    .tag(i)
                    .overlay(alignment: .bottom) { caption(p) }
            }
        }
        .tabViewStyle(.page(indexDisplayMode: .never))
        .background(Color.black)
        .ignoresSafeArea(edges: .bottom)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { current = photos.firstIndex(of: start) ?? 0 }
    }

    private func caption(_ p: Photo) -> some View {
        VStack(spacing: 2) {
            if let t = p.takenAt?.prefix(10) {
                Text(String(t)).font(.caption.monospaced())
            }
            if let place = p.place, !place.isEmpty {
                Text(place).font(.caption2)
            }
        }
        .foregroundStyle(.white.opacity(0.85))
        .padding(8)
        .background(.black.opacity(0.35), in: Capsule())
        .padding(.bottom, 28)
    }
}

/// ⚠ The large view takes 1200 and not 2800: on a phone you cannot see the
///   difference, and on a mobile network the image arrives in a second
///   instead of three.
struct ZoomableImage: View {
    let id: Int
    @State private var scale: CGFloat = 1
    @State private var last: CGFloat = 1

    var body: some View {
        RemoteImage(id: id, width: 1200, contentMode: .fit)
            .scaleEffect(scale)
            .gesture(
                MagnificationGesture()
                    .onChanged { v in scale = min(max(last * v, 1), 5) }
                    .onEnded { _ in last = scale }
            )
            .onTapGesture(count: 2) {
                withAnimation(.spring(duration: 0.25)) {
                    scale = scale > 1 ? 1 : 2.5
                    last = scale
                }
            }
    }
}

// MARK: - Den Deel-Link

struct ShareSheetView: View {
    let link: ShareResult
    @Environment(\.dismiss) private var dismiss

    /// The link and the password together -- one message you can send.
    private var message: String {
        "\(link.url)\n\nPassword: \(link.password)"
    }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 18) {
                Text("A link for someone without an account.")
                    .font(.callout).foregroundStyle(Theme.inkSoft)

                VStack(alignment: .leading, spacing: 4) {
                    Text("LINK").font(.caption2.monospaced()).foregroundStyle(Theme.inkMute)
                    Text(link.url).font(.footnote.monospaced()).textSelection(.enabled)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("PASSWORD").font(.caption2.monospaced()).foregroundStyle(Theme.inkMute)
                    Text(link.password).font(.title3.monospaced())
                        .foregroundStyle(Theme.safelight).textSelection(.enabled)
                }
                if let e = link.expiresAt {
                    Text("Runs out \(e.prefix(10))")
                        .font(.caption).foregroundStyle(Theme.inkMute)
                }

                // ⚠ D'Passwuert geet MAT am selwechte Message. Eng Famill
                //   sends the link over one channel anyway -- and a link
                //   without its password is just a wall to the person opening it.
                ShareLink(item: message) {
                    Label("Send", systemImage: "square.and.arrow.up")
                        .frame(maxWidth: .infinity).padding(.vertical, 10)
                }
                .buttonStyle(.borderedProminent)

                Spacer()
            }
            .padding(20)
            .background(Theme.ground)
            .navigationTitle("Share")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
            }
        }
    }
}
