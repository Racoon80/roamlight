//  One photograph, large -- and the link for sharing an album.

import AVKit
import SwiftUI

struct PhotoView: View {
    let photos: [Photo]
    let start: Photo

    @EnvironmentObject var state: AppState

    @State private var index = 0
    /// How far the pager has been dragged, before it settles on a page.
    @State private var pageDrag: CGFloat = 0

    // The zoom belongs to the page being looked at, and is reset when the page
    // changes. ⚠ It lives HERE and not inside the image, because the same drag
    // has to mean two different things — move the photograph, or turn the page
    // — and only one place can decide which.
    @State private var zoom: CGFloat = 1
    @State private var zoomStart: CGFloat = 1
    @State private var pan: CGSize = .zero
    @State private var panStart: CGSize = .zero

    private var zoomed: Bool { zoom > 1.001 }
    /// ⚠ A video is left alone by the pinch and the pan: the player has its
    ///   own controls, and two things fighting over the same drag is how a
    ///   scrub bar becomes unusable. Turning the page still works.
    private var onVideo: Bool {
        photos.indices.contains(index) && photos[index].isVideo
    }

    var body: some View {
        GeometryReader { geo in
            let page = geo.size

            HStack(spacing: 0) {
                ForEach(Array(photos.enumerated()), id: \.offset) { i, p in
                    Group {
                        if p.isVideo {
                            // ⚠ Only the page you are on gets a player. Three
                            //   AVPlayers side by side would each open their own
                            //   connection and buffer — on a phone that is how
                            //   an app gets killed for using too much.
                            VideoPage(photo: p, active: i == index)
                        } else {
                            RemoteImage(id: p.id, width: 1200, rev: p.rev ?? 0,
                                        contentMode: .fit)
                                .scaleEffect(i == index ? zoom : 1)
                                .offset(i == index ? pan : .zero)
                        }
                    }
                    .frame(width: page.width, height: page.height)
                    // ⚠ Clipped per page: a zoomed photograph must not spill
                    //   over its neighbour — which is exactly what the
                    //   screenshot of the bug showed.
                    .clipped()
                }
            }
            .frame(width: page.width * CGFloat(max(photos.count, 1)),
                   alignment: .leading)
            .offset(x: -CGFloat(index) * page.width + pageDrag)
            .contentShape(Rectangle())
            .gesture(
                MagnificationGesture()
                    .onChanged { v in
                        guard !onVideo else { return }
                        zoom = min(max(zoomStart * v, 1), 5)
                        pan = clamped(pan, in: page)
                    }
                    .onEnded { _ in
                        zoomStart = zoom
                        if !zoomed { withAnimation(.spring(duration: 0.2)) { reset() } }
                        panStart = pan
                    }
                    .simultaneously(with:
                        DragGesture()
                            .onChanged { v in
                                if zoomed && !onVideo {
                                    pan = clamped(CGSize(
                                        width: panStart.width + v.translation.width,
                                        height: panStart.height + v.translation.height),
                                        in: page)
                                } else {
                                    pageDrag = resist(v.translation.width, page: page)
                                }
                            }
                            .onEnded { v in
                                if zoomed {
                                    panStart = pan
                                } else {
                                    settle(v, page: page)
                                }
                            })
            )
            .onTapGesture(count: 2) {
                guard !onVideo else { return }
                withAnimation(.spring(duration: 0.25)) {
                    if zoomed { reset() } else { zoom = 2.5; zoomStart = 2.5 }
                }
            }
        }
        // ⚠ Only the BACKGROUND runs under the tab bar. The caption is an
        //   overlay on the content, so it stays above it -- put the whole view
        //   outside the safe area and the date ends up behind the tabs.
        .background(Color.black.ignoresSafeArea())
        .overlay(alignment: .bottom) {
            if photos.indices.contains(index) { caption(photos[index]) }
        }
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { index = photos.firstIndex(of: start) ?? 0 }
    }

    // MARK: - Rechnen

    /// How far the photograph may be moved before its own edge would come
    /// inside the screen. ⚠ Without this a zoomed photograph can be dragged
    /// off into the black and there is no way back except pinching out.
    private func clamped(_ p: CGSize, in page: CGSize) -> CGSize {
        let shown = fitted(in: page)
        let maxX = max(0, (shown.width * zoom - page.width) / 2)
        let maxY = max(0, (shown.height * zoom - page.height) / 2)
        return CGSize(width: min(max(p.width, -maxX), maxX),
                      height: min(max(p.height, -maxY), maxY))
    }

    /// The size the photograph really occupies inside the page, `.fit` being
    /// what the image uses. Falls back to a square when the server sent no
    /// measurements — then the clamp is a little generous, never wrong.
    private func fitted(in page: CGSize) -> CGSize {
        guard page.width > 0, page.height > 0 else { return page }
        let r = photos.indices.contains(index) ? photos[index].ratio : 1
        return r > page.width / page.height
            ? CGSize(width: page.width, height: page.width / r)
            : CGSize(width: page.height * r, height: page.height)
    }

    /// The first and the last page pull back instead of sliding into black.
    private func resist(_ dx: CGFloat, page: CGSize) -> CGFloat {
        if (index == 0 && dx > 0) || (index == photos.count - 1 && dx < 0) {
            return dx / 3
        }
        return dx
    }

    private func settle(_ v: DragGesture.Value, page: CGSize) {
        // A quick flick counts as well as a long drag -- that is what
        // `predictedEndTranslation` is for.
        let travelled = max(abs(v.translation.width), abs(v.predictedEndTranslation.width))
        let far = travelled > page.width / 4
        var next = index
        if far && v.translation.width < 0 { next = min(index + 1, photos.count - 1) }
        if far && v.translation.width > 0 { next = max(index - 1, 0) }
        withAnimation(.easeOut(duration: 0.22)) {
            pageDrag = 0
            if next != index {
                index = next
                reset()          // a new photograph starts unzoomed
            }
        }
    }

    private func reset() {
        zoom = 1
        zoomStart = 1
        pan = .zero
        panStart = .zero
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
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(.black.opacity(0.45), in: Capsule())
        .padding(.bottom, 12)
    }
}

/// One video, with the system's own player and its own controls.
///
/// ⚠ The player is made when this page becomes the one being looked at, and
///   thrown away when it stops being it. Otherwise every video in the album
///   would sit there holding a connection open behind your back.
struct VideoPage: View {
    let photo: Photo
    let active: Bool

    @EnvironmentObject var state: AppState
    @State private var player: AVPlayer?
    @State private var failed: String?

    var body: some View {
        ZStack {
            Color.black
            if let player {
                VideoPlayer(player: player)
            } else if let failed {
                VStack(spacing: 10) {
                    Image(systemName: "exclamationmark.triangle")
                    Text(failed).font(.footnote).multilineTextAlignment(.center)
                }
                .foregroundStyle(Theme.inkSoft)
                .padding(32)
            } else {
                // The poster frame, so there is something to look at while the
                // address is being fetched.
                RemoteImage(id: photo.id, width: 1200, rev: photo.rev ?? 0,
                            contentMode: .fit)
                ProgressView().tint(.white)
            }
        }
        .task(id: active) {
            guard active else {
                player?.pause()
                player = nil
                return
            }
            do {
                player = AVPlayer(url: try await state.api.videoURL(photo.id))
                player?.play()
            } catch {
                failed = error.localizedDescription
            }
        }
        .onDisappear {
            player?.pause()
            player = nil
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
