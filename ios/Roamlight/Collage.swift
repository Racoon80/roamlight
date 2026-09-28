//  The album as the website shows it: prints scattered on a table.
//
//  ⚠ A port of the collage in `static/site.js` and `.snap` in `site.css`, and
//    the numbers are theirs -- change one side and the other should follow:
//      * rows that fill the width, each photograph at its own proportions
//        (the last row is not blown up: it keeps the target height),
//      * target row height 170 pt on a phone, 250 on a tablet,
//      * the prints overlap by 2 × 7 pt,
//      * each is turned by up to ±5°, and which one lies on top is a fixed
//        "random" per position -- the same formula, so the same album looks the
//        same every time it is opened,
//      * a white border (6 pt, 4 on a phone) and a two-layer shadow.

import SwiftUI

/// The fixed "random" of site.js: `sin(i * 99.13 + 0.7) * 10000`, fractional part.
func collageRandom(_ i: Int) -> Double {
    let x = sin(Double(i) * 99.13 + 0.7) * 10000
    return x - floor(x)
}

struct Collage<Cell: View>: View {
    let photos: [Photo]
    let width: CGFloat
    @ViewBuilder let cell: (Photo) -> Cell
    var onLastRow: () -> Void = {}

    private static var margin: CGFloat { 7 }            // == MARGIN in site.js

    private struct Item: Identifiable {
        let index: Int
        let photo: Photo
        let size: CGSize
        var id: Int { photo.id }
    }

    private struct Row: Identifiable {
        let items: [Item]
        var id: Int { items.first?.photo.id ?? 0 }
    }

    private var rows: [Row] {
        let W = width
        guard W > 0 else { return [] }
        let targetH: CGFloat = W < 640 ? 170 : (W < 1100 ? 250 : 300)
        let gap = -2 * Self.margin
        var out: [Row] = []
        var row: [(Int, Photo, CGFloat)] = []
        var sumAR: CGFloat = 0

        func flush(last: Bool) {
            guard !row.isEmpty else { return }
            var h = (W - gap * CGFloat(row.count - 1)) / sumAR
            if last { h = min(h, targetH) }
            out.append(Row(items: row.map { Item(index: $0.0, photo: $0.1,
                                                   size: CGSize(width: $0.2 * h, height: h)) }))
            row = []; sumAR = 0
        }
        for (i, p) in photos.enumerated() {
            let ar = max(0.2, min(5, p.ratio == 1 && p.width == nil ? 1.5 : p.ratio))
            row.append((i, p, ar)); sumAR += ar
            if sumAR * targetH + gap * CGFloat(row.count - 1) >= W { flush(last: false) }
        }
        flush(last: true)
        return out
    }

    var body: some View {
        let all = rows
        LazyVStack(spacing: -2 * Self.margin) {
            ForEach(all) { r in
                HStack(spacing: -2 * Self.margin) {
                    ForEach(r.items) { it in
                        cell(it.photo)
                            .frame(width: it.size.width, height: it.size.height)
                            .rotationEffect(.degrees((collageRandom(it.index) * 2 - 1) * 5))
                            .zIndex(Double(Int(collageRandom(it.index + 7) * 8) + 1))
                    }
                }
                .frame(maxWidth: .infinity)          // a short last row sits in the middle
                .onAppear { if r.id == all.last?.id { onLastRow() } }
            }
        }
    }
}

/// One print: the photograph inside its white border, with the shadow.
struct Print: View {
    let photo: Photo
    @Environment(\.horizontalSizeClass) private var size

    var body: some View {
        let border: CGFloat = size == .compact ? 4 : 6
        RemoteImage(id: photo.id, width: 800, rev: photo.rev ?? 0)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()
            .overlay { if photo.isVideo { PlayMark(duration: photo.durationS) } }
            .padding(border)
            .background(Theme.paper, in: RoundedRectangle(cornerRadius: 2))
            .shadow(color: .black.opacity(0.42), radius: 3, y: 2)
            .shadow(color: .black.opacity(0.5), radius: 10, y: 8)
    }
}

/// The site's play mark: a dark disc with a light triangle, and how long.
struct PlayMark: View {
    let duration: Int?

    var body: some View {
        ZStack {
            Circle()
                .fill(Color(red: 0.08, green: 0.07, blue: 0.06).opacity(0.55))
                .overlay(Circle().stroke(Theme.paper.opacity(0.55), lineWidth: 1))
                .frame(width: 34, height: 34)
            Image(systemName: "play.fill")
                .font(.system(size: 13))
                .foregroundStyle(Theme.paper)
                .offset(x: 1.5)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .overlay(alignment: .bottomTrailing) {
            if let s = duration, s > 0 {
                Text("\(s / 60):\(String(format: "%02d", s % 60))")
                    .font(Theme.mono(9)).tracking(0.6)
                    .foregroundStyle(Theme.paper)
                    .padding(.horizontal, 5).padding(.vertical, 2)
                    .background(Color(red: 0.08, green: 0.07, blue: 0.06).opacity(0.7),
                                in: RoundedRectangle(cornerRadius: 2))
                    .padding(6)
            }
        }
    }
}

/// A touch does what the pointer does on the site: the print straightens,
/// lifts and comes to the front.
struct PrintPress: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 1.08 : 1)
            .offset(y: configuration.isPressed ? -5 : 0)
            .zIndex(configuration.isPressed ? 600 : 0)
            .animation(.spring(duration: 0.3), value: configuration.isPressed)
    }
}
