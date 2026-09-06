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

    func load(_ api: API, id: Int, width: Int) async -> UIImage? {
        let key = "\(id)-\(width)"
        if let hit = cached(key) { return hit }
        guard let data = try? await api.image(id, width: width),
              let img = UIImage(data: data) else { return nil }
        cache.setObject(img, forKey: key as NSString, cost: data.count)
        return img
    }
}

struct RemoteImage: View {
    let id: Int
    let width: Int
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
        .task(id: "\(id)-\(width)") {
            if let hit = ImageStore.shared.cached("\(id)-\(width)") {
                image = hit
                return
            }
            image = await ImageStore.shared.load(state.api, id: id, width: width)
        }
    }
}
