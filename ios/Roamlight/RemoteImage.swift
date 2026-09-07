//  E Bild vum Site.
//
//  ⚠ `AsyncImage` does NOT work here: it cannot set a header, and without the
//    `Authorization` header every photograph comes back as a 302 to the sign-in
//    page. So: a small loader of our own -- with a cache, because a gallery asks
//    for the same photograph again and again while scrolling.

import SwiftUI
import UIKit

@MainActor
final class ImageStore {
    static let shared = ImageStore()

    /// ⚠ `NSCache` and not a dictionary: under memory pressure it clears itself
    /// out. A gallery of 500 photographs would otherwise kill the app.
    private let cache = NSCache<NSString, UIImage>()

    private init() { cache.totalCostLimit = 80 * 1024 * 1024 }

    func cached(_ key: String) -> UIImage? { cache.object(forKey: key as NSString) }

    func load(_ api: API, id: Int, width: Int, rev: Int) async -> UIImage? {
        let key = "\(id)-\(width)-\(rev)"
        if let hit = cached(key) { return hit }
        guard let data = try? await api.image(id, width: width, rev: rev),
              let img = UIImage(data: data) else { return nil }
        cache.setObject(img, forKey: key as NSString, cost: data.count)
        return img
    }

    /// ⚠ Emptied when the device is taken off the list. The photographs sit in
    ///   memory without a token of their own -- leaving them there would mean
    ///   the next person to open the app sees the last family's pictures
    ///   before the sign-in screen has even appeared.
    func clear() { cache.removeAllObjects() }
}

struct RemoteImage: View {
    let id: Int
    let width: Int
    /// ⚠ The server bumps `photos.rev` when a photograph is turned. Without it
    ///   in the key AND in the address, a rotated photograph keeps showing the
    ///   old way round -- out of this cache, and out of the URL cache
    ///   underneath, whose answers say `immutable` for a year.
    var rev: Int = 0
    var contentMode: ContentMode = .fill

    @EnvironmentObject private var state: AppState
    @State private var image: UIImage?

    var body: some View {
        ZStack {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: contentMode)
            } else {
                Theme.groundWarm
                    .overlay(ProgressView().tint(Theme.inkMute).scaleEffect(0.7))
            }
        }
        .task(id: "\(id)-\(width)-\(rev)") {
            if let hit = ImageStore.shared.cached("\(id)-\(width)-\(rev)") {
                image = hit
                return
            }
            image = await ImageStore.shared.load(state.api, id: id, width: width, rev: rev)
        }
    }
}
