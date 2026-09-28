//  The slideshow: one photograph after another, full screen.
//
//  ⚠ The timing, and why:
//    * 5 seconds a photograph (3 and 8 to choose from). Long enough to look,
//      short enough that an album of a hundred does not take a quarter of an
//      hour.
//    * a 1-second CROSSFADE, one photograph straight into the next. A fade out
//      to black and in again blinks every five seconds; the crossfade does not.
//    * a slow drift and zoom while a photograph stands (Ken Burns), up to 6 %,
//      alternating direction, so a still picture does not look frozen. Only on
//      photographs: a video moves by itself.
//    * a video plays to its end, then the show goes on.
//
//  ⚠ The screen stays on while the show runs, and only then.

import AVKit
import SwiftUI

struct SlideshowView: View {
    let album: Album?
    var query: String? = nil
    /// What the album had already loaded -- the show starts at once with that
    /// and fetches the rest of the album while it plays.
    let start: [Photo]
    let total: Int

    @EnvironmentObject var state: AppState
    @Environment(\.dismiss) private var dismiss
    @AppStorage("slideSeconds") private var seconds = 5.0

    @State private var photos: [Photo] = []
    @State private var index = 0
    @State private var paused = false
    @State private var controls = true
    @State private var videoDone = 0
    /// The photograph being faded OUT of, while the next fades in.
    @State private var previous: Int?

    /// What is on screen, bottom first.
    private var layers: [Int] {
        [previous, index].compactMap { $0 }
            .filter { photos.indices.contains($0) }
            .reduce(into: [Int]()) { if !$0.contains($1) { $0.append($1) } }
    }

    private let fade = 1.0

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            // ⚠ A real crossfade, built by hand: the photograph before stays
            //   fully there UNDERNEATH while the new one fades in on top, and
            //   only then goes. SwiftUI's own transitions fade both at once,
            //   each half transparent in the middle over black -- measured in
            //   the simulator as a dip to nearly black every five seconds.
            //   Keyed by the photograph, so the one that becomes "before" is
            //   the SAME view and its slow drift does not start over.
            ForEach(layers, id: \.self) { i in
                let p = photos[i]
                FadeIn(duration: fade) {
                    if p.isVideo {
                        SlideVideo(photo: p) { videoDone += 1 }
                    } else {
                        KenBurns(photo: p, index: i, seconds: seconds + fade)
                    }
                }
                .ignoresSafeArea()
            }

            if controls { overlay }
        }
        .statusBarHidden(!controls)
        .contentShape(Rectangle())
        .onTapGesture { withAnimation(.easeOut(duration: 0.25)) { controls.toggle() } }
        .onAppear {
            photos = start
            UIApplication.shared.isIdleTimerDisabled = true
        }
        .onDisappear { UIApplication.shared.isIdleTimerDisabled = false }
        .task { await loadRest() }
        // The clock. ⚠ Restarted whenever the photograph, the pause or the speed
        //   changes -- one task, cancelled and begun again, never two running.
        .task(id: "\(index)-\(paused)-\(seconds)") { await tick() }
        .onChange(of: videoDone) { _, _ in advance() }
        .task { try? await Task.sleep(for: .seconds(2.5)); withAnimation { controls = false } }
    }

    // MARK: - Bedienung

    private var overlay: some View {
        VStack {
            HStack {
                Button { dismiss() } label: {
                    Image(systemName: "xmark").font(.system(size: 17, weight: .semibold))
                        .padding(12).background(.black.opacity(0.45), in: Circle())
                }
                Spacer()
                if photos.indices.contains(index), let t = photos[index].takenAt?.prefix(10) {
                    Text(String(t)).font(Theme.mono(11)).tracking(1.2)
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(.black.opacity(0.45), in: Capsule())
                }
                Spacer()
                Text("\(index + 1) / \(max(total, photos.count))")
                    .font(Theme.mono(11)).tracking(1.2)
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .background(.black.opacity(0.45), in: Capsule())
            }
            Spacer()
            HStack(spacing: 18) {
                Button { step(-1) } label: { Image(systemName: "backward.fill") }
                Button { paused.toggle() } label: {
                    Image(systemName: paused ? "play.fill" : "pause.fill").font(.system(size: 22))
                }
                Button { step(1) } label: { Image(systemName: "forward.fill") }
                Divider().frame(height: 22).overlay(Color.white.opacity(0.3))
                // 3, 5 or 8 seconds -- kept for next time.
                ForEach([3.0, 5.0, 8.0], id: \.self) { s in
                    Button { seconds = s } label: {
                        Text("\(Int(s)) s").font(Theme.mono(12, medium: seconds == s))
                            .foregroundStyle(seconds == s ? Theme.safelight : .white.opacity(0.8))
                    }
                }
            }
            .font(.system(size: 18))
            .padding(.horizontal, 20).padding(.vertical, 12)
            .background(.black.opacity(0.5), in: Capsule())
            .padding(.bottom, 24)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 16)
        .transition(.opacity)
    }

    // MARK: - Ofleef

    private func tick() async {
        guard !paused, photos.indices.contains(index) else { return }
        prefetch(index + 1)
        // A video decides for itself when it is done (see `videoDone`).
        guard !photos[index].isVideo else { return }
        try? await Task.sleep(for: .seconds(seconds))
        guard !Task.isCancelled else { return }
        advance()
    }

    private func advance() { step(1) }

    private func step(_ by: Int) {
        guard !photos.isEmpty else { return }
        // Round and round: after the last comes the first again.
        let next = (index + by + photos.count) % photos.count
        guard next != index else { return }
        previous = index
        index = next
        let was = previous
        Task {
            try? await Task.sleep(for: .seconds(fade + 0.1))
            if previous == was { previous = nil }
        }
    }

    /// The next photograph is fetched while this one stands -- otherwise the
    /// crossfade would fade into a grey placeholder.
    private func prefetch(_ i: Int) {
        guard photos.indices.contains(i), !photos[i].isVideo else { return }
        let p = photos[i]
        Task { _ = await ImageStore.shared.load(state.api, id: p.id, width: 2000, rev: p.rev ?? 0) }
    }

    /// The rest of the album, page by page, while the show is already running.
    private func loadRest() async {
        guard album != nil || query != nil else { return }
        var page = 1
        while true {
            guard let res = try? await state.api.photos(album: album, page: page, query: query)
            else { return }
            let known = Set(photos.map(\.id))
            photos += res.photos.filter { !known.contains($0.id) }
            if res.page >= res.pages { return }
            page += 1
        }
    }
}

/// Fades its content in once, when it first appears -- and never out.
private struct FadeIn<Content: View>: View {
    let duration: Double
    @ViewBuilder let content: Content
    @State private var shown = false

    var body: some View {
        content
            .opacity(shown ? 1 : 0)
            .onAppear { withAnimation(.easeInOut(duration: duration)) { shown = true } }
    }
}

/// A photograph that drifts slowly while it stands.
private struct KenBurns: View {
    let photo: Photo
    let index: Int
    let seconds: Double

    @State private var moved = false

    var body: some View {
        // Alternating: in and to the right, then out and to the left, ...
        let even = index % 2 == 0
        RemoteImage(id: photo.id, width: 2000, rev: photo.rev ?? 0, contentMode: .fit)
            .scaleEffect(moved ? (even ? 1.06 : 1.0) : (even ? 1.0 : 1.06))
            .offset(x: moved ? (even ? 10 : -10) : 0, y: moved ? (even ? -6 : 6) : 0)
            .onAppear {
                withAnimation(.linear(duration: seconds)) { moved = true }
            }
    }
}

/// A video in the show: plays once, then says so.
private struct SlideVideo: View {
    let photo: Photo
    let done: () -> Void

    @EnvironmentObject var state: AppState
    @State private var player: AVPlayer?

    var body: some View {
        ZStack {
            Color.black
            if let player {
                VideoPlayer(player: player)
            } else {
                RemoteImage(id: photo.id, width: 1200, rev: photo.rev ?? 0, contentMode: .fit)
            }
        }
        .task {
            guard let url = try? await state.api.videoURL(photo.id) else { done(); return }
            let p = AVPlayer(url: url)
            player = p
            p.play()
            for await _ in NotificationCenter.default.notifications(
                named: .AVPlayerItemDidPlayToEndTime, object: p.currentItem) {
                done()
                break
            }
        }
        .onDisappear { player?.pause(); player = nil }
    }
}
