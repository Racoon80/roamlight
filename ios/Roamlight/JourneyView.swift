//  D'Ouverture vun engem Album: wéi ee bis dohinner komm ass.
//
//  ⚠ The website plays this once when an album opens, and the app did not have
//    it at all. It is not decoration: an album is a journey, and the first
//    thing you see should say where it went.
//
//  ⚠ The tiles come THROUGH the site (`/tiles/…`), not from openstreetmap.org.
//    The website fetches them directly — a decision with a price, and one the
//    app does not have to repeat: a phone asking for tiles tells a stranger
//    roughly where the family's albums are.

import SwiftUI

// MARK: - Rechnen

/// Web Mercator, the projection every slippy map uses. Returns 0…1 on both
/// axes, so the zoom is just a multiplication afterwards.
private func project(_ lat: Double, _ lon: Double) -> (x: Double, y: Double) {
    let la = min(max(lat, -85.05112878), 85.05112878) * .pi / 180
    return ((lon + 180) / 360,
            (1 - log(tan(la) + 1 / cos(la)) / .pi) / 2)
}

private let tileSize: Double = 256

/// How long the whole journey takes to draw itself.
private let RUN: Double = 3.4

/// The points of one leg: the real road when there is one, otherwise an arc.
///
/// ⚠ A straight line between two far-apart places looks wrong on a flat map —
///   a flight is drawn as a curve on paper for the same reason. So a leg with
///   no road gets a gentle bow, always to the same side, so two legs never
///   overlap into a single stroke.
private func legPoints(_ from: (Double, Double), _ leg: Journey.Leg) -> [(Double, Double)] {
    if let route = leg.route, route.count > 1 {
        return route.compactMap { $0.count >= 2 ? ($0[0], $0[1]) : nil }
    }
    let to = (leg.to[0], leg.to[1])
    let steps = 48
    let a = project(from.0, from.1), b = project(to.0, to.1)
    // The bow, as a fraction of the distance -- so a short hop is nearly
    // straight and a long flight arcs properly.
    let dx = b.x - a.x, dy = b.y - a.y
    let bow = 0.16
    return (0...steps).map { i in
        let t = Double(i) / Double(steps)
        let x = a.x + dx * t
        let y = a.y + dy * t
        // Perpendicular offset, biggest in the middle.
        let lift = sin(t * .pi) * bow
        let px = x - dy * lift
        let py = y + dx * lift
        // …and back to degrees, because everything downstream projects again.
        let lon = px * 360 - 180
        let n = .pi - 2 * .pi * py
        let lat = 180 / .pi * atan(0.5 * (exp(n) - exp(-n)))
        return (lat, lon)
    }
}

private func glyph(_ transport: String) -> String {
    switch transport {
    case "plane": return "✈"
    case "train": return "🚂"
    case "bus":   return "🚌"
    default:      return "🚗"
    }
}

// MARK: - D'Kacheln

@MainActor
final class TileStore: ObservableObject {
    static let shared = TileStore()

    /// ⚠ An `NSCache`, not a dictionary — the same reason as for the
    ///   photographs: it lets go of itself under memory pressure. A plain
    ///   dictionary here grew by about fifteen tiles for every album ever
    ///   opened and never gave any of them back.
    private let cache = NSCache<NSString, UIImage>()
    @Published private(set) var version = 0

    private init() { cache.totalCostLimit = 24 * 1024 * 1024 }

    func cached(_ z: Int, _ x: Int, _ y: Int) -> UIImage? {
        cache.object(forKey: "\(z)/\(x)/\(y)" as NSString)
    }

    func load(_ api: API, _ z: Int, _ x: Int, _ y: Int) async {
        let key = "\(z)/\(x)/\(y)" as NSString
        guard cache.object(forKey: key) == nil else { return }
        guard let d = try? await api.tile(z: z, x: x, y: y),
              let img = UIImage(data: d) else { return }
        cache.setObject(img, forKey: key, cost: d.count)
        version += 1
    }
}

// MARK: - D'Animatioun

struct JourneyView: View {
    let journey: Journey
    let onDone: () -> Void

    @EnvironmentObject var state: AppState
    // ⚠ `@ObservedObject`: `@StateObject` is for something this view OWNS,
    //   and a shared store is not that.
    @ObservedObject private var tiles = TileStore.shared
    /// ⚠ The clock, not an animated number. A `Canvas` draws once for the value
    ///   it is handed: `withAnimation` interpolates VIEW inputs, and the
    ///   closure of a Canvas is not one. The line therefore appeared finished
    ///   and the vehicle sat at the destination. `TimelineView` re-draws, and
    ///   the progress is worked out from how long it has been running.
    @State private var began: Date?
    @State private var zoom = 5
    @State private var origin: CGPoint = .zero          // top-left, in pixels at `zoom`
    @State private var laid = false

    /// Every point of the whole journey, leg by leg.
    private var path: [[(Double, Double)]] {
        var out: [[(Double, Double)]] = []
        var from = (journey.from[0], journey.from[1])
        for leg in journey.legs where leg.to.count >= 2 {
            out.append(legPoints(from, leg))
            from = (leg.to[0], leg.to[1])
        }
        return out
    }

    var body: some View {
        GeometryReader { geo in
            let size = geo.size
            ZStack {
                // ⚠ Not black. A tile that has not arrived yet leaves a hole,
                //   and a black hole in the middle of a map reads as broken;
                //   the site's own warm ground reads as a map still painting.
                Theme.groundWarm
                TimelineView(.animation) { tl in
                    let p = progress(at: tl.date)
                    Canvas { ctx, _ in draw(&ctx, size: size, progress: p) }
                        .allowsHitTesting(false)
                }
            }
            .onAppear {
                guard !laid else { return }
                laid = true
                fit(into: size)
                began = Date()
                // ⚠ The drawing does not decide when it is over -- this does.
                //   A journey nobody can leave is a trap.
                Task {
                    try? await Task.sleep(for: .seconds(RUN + 0.8))
                    onDone()
                }
            }
        }
        .overlay(alignment: .topTrailing) {
            Button("skip ×") { onDone() }
                .font(.footnote)
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(.black.opacity(0.5), in: Capsule())
                .foregroundStyle(.white.opacity(0.8))
                .padding(16)
        }
        // ⚠ The tiles are OpenStreetMap's, and their licence (ODbL) asks for
        //   the credit to be visible WHEREVER they are shown. The website puts
        //   it there through Leaflet; this canvas draws the tiles itself, so
        //   nothing would put it here. Bottom leading, out of the way of the
        //   departure line in the middle and the "skip" at the top.
        .overlay(alignment: .bottomLeading) {
            Text("© OpenStreetMap")
                .font(.system(size: 10))
                .foregroundStyle(.white.opacity(0.75))
                .padding(.horizontal, 6).padding(.vertical, 3)
                .background(.black.opacity(0.4), in: Capsule())
                .padding(.leading, 10)
                .padding(.bottom, 10)
        }
        .overlay(alignment: .bottom) {
            if let d = journey.departure, !d.isEmpty {
                Text("\(d)  \(journey.legs.map { glyph($0.transport) }.joined())")
                    .font(.footnote.monospaced())
                    .foregroundStyle(.white.opacity(0.85))
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(.black.opacity(0.45), in: Capsule())
                    .padding(.bottom, 22)
            }
        }
        .ignoresSafeArea()
    }

    // MARK: - Molen

    /// How far along, from the clock. Eases in and out, like the website's.
    private func progress(at now: Date) -> Double {
        guard let began else { return 0 }
        let t = min(max(now.timeIntervalSince(began) / RUN, 0), 1)
        return t * t * (3 - 2 * t)              // smoothstep
    }

    private func draw(_ ctx: inout GraphicsContext, size: CGSize, progress: Double) {
        _ = tiles.version                                  // redraw as tiles land
        // 1. The tiles
        let z = zoom
        let x0 = Int(floor(origin.x / tileSize)), x1 = Int(floor((origin.x + size.width) / tileSize))
        let y0 = Int(floor(origin.y / tileSize)), y1 = Int(floor((origin.y + size.height) / tileSize))
        let span = 1 << z
        for tx in x0...x1 {
            for ty in y0...y1 where ty >= 0 && ty < span {
                let wrapped = ((tx % span) + span) % span
                guard let img = tiles.cached(z, wrapped, ty) else { continue }
                let r = CGRect(x: Double(tx) * tileSize - origin.x,
                               y: Double(ty) * tileSize - origin.y,
                               width: tileSize, height: tileSize)
                ctx.opacity = 0.55        // the map stays behind the line
                ctx.draw(Image(uiImage: img), in: r)
                ctx.opacity = 1
            }
        }

        // 2. The line, as far as it has got
        let legs = path
        let total = legs.reduce(0) { $0 + max($1.count - 1, 0) }
        guard total > 0 else { return }
        var walked = 0
        var head: CGPoint?
        var headGlyph = "🚗"
        for (i, pts) in legs.enumerated() {
            let screen = pts.map { point($0.0, $0.1) }
            var line = Path()
            var drawn = 0
            for (k, p) in screen.enumerated() {
                let done = Double(walked + k) / Double(total)
                if done > progress { break }
                if k == 0 { line.move(to: p) } else { line.addLine(to: p) }
                drawn = k
            }
            if drawn > 0 {
                ctx.stroke(line, with: .color(Color(red: 0.416, green: 0.663, blue: 0.878)),
                           style: StrokeStyle(lineWidth: 2.5, lineCap: .round,
                                              dash: [7, 6]))
                head = screen[drawn]
                headGlyph = glyph(journey.legs[min(i, journey.legs.count - 1)].transport)
            }
            walked += max(pts.count - 1, 0)
        }

        // 3. The places, and the vehicle on the line
        var dots = [point(journey.from[0], journey.from[1])]
        dots += journey.legs.compactMap { $0.to.count >= 2 ? point($0.to[0], $0.to[1]) : nil }
        for d in dots {
            ctx.fill(Path(ellipseIn: CGRect(x: d.x - 4, y: d.y - 4, width: 8, height: 8)),
                     with: .color(Color(red: 0.416, green: 0.663, blue: 0.878)))
        }
        if let h = head {
            ctx.draw(Text(headGlyph).font(.system(size: 26)), at: h)
        }
    }

    private func point(_ lat: Double, _ lon: Double) -> CGPoint {
        let p = project(lat, lon)
        let scale = Double(1 << zoom) * tileSize
        return CGPoint(x: p.x * scale - origin.x, y: p.y * scale - origin.y)
    }

    /// Choose the closest zoom at which the whole journey still fits, then
    /// fetch exactly the tiles that are on screen.
    private func fit(into size: CGSize) {
        var pts = [(journey.from[0], journey.from[1])]
        for leg in path { pts += leg }
        guard !pts.isEmpty else { return }
        let xs = pts.map { project($0.0, $0.1).x }, ys = pts.map { project($0.0, $0.1).y }
        let minX = xs.min()!, maxX = xs.max()!, minY = ys.min()!, maxY = ys.max()!
        let pad = 0.82                                   // a margin, so nothing touches the edge
        var z = 2
        for cand in stride(from: 10, through: 2, by: -1) {
            let scale = Double(1 << cand) * tileSize
            if (maxX - minX) * scale <= size.width * pad,
               (maxY - minY) * scale <= size.height * pad {
                z = cand
                break
            }
        }
        zoom = z
        let scale = Double(1 << z) * tileSize
        let cx = (minX + maxX) / 2 * scale, cy = (minY + maxY) / 2 * scale
        origin = CGPoint(x: cx - size.width / 2, y: cy - size.height / 2)

        let x0 = Int(floor(origin.x / tileSize)), x1 = Int(floor((origin.x + size.width) / tileSize))
        let y0 = Int(floor(origin.y / tileSize)), y1 = Int(floor((origin.y + size.height) / tileSize))
        let span = 1 << z
        let api = state.api
        for tx in x0...x1 {
            for ty in y0...y1 where ty >= 0 && ty < span {
                let wrapped = ((tx % span) + span) % span
                Task { await tiles.load(api, z, wrapped, ty) }
            }
        }
    }
}
