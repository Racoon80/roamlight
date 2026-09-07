//  What the server sends back. The fields are named here exactly as they are
//  there -- ⚠ rename one and you have to add a `CodingKeys`, or an empty list
//    comes back and it looks like "no photographs".

import Foundation

struct Pairing: Decodable {
    let token: String
    let user: String
}

struct Me: Decodable {
    let user: String
    let email: String
    let groups: [String]
    let may: Rights
    let site: String
    let sizes: [Int]

    struct Rights: Decodable {
        let view: Bool
        let upload: Bool
        let share: Bool
        let admin: Bool
    }
}

struct AlbumList: Decodable {
    let albums: [Album]
}

struct Album: Decodable, Identifiable, Hashable {
    let year: String
    let country: String
    let event: String
    let title: String
    let n: Int
    let cover: Int?
    let coverRev: Int?
    let latest: String?

    // A stable key for SwiftUI -- the same shape as the album_key
    // um Server (`<Joer>/<Land>/<Numm>`).
    var id: String { "\(year)/\(country)/\(event)" }

    enum CodingKeys: String, CodingKey {
        case year, country, event, title, n, cover, latest
        case coverRev = "cover_rev"
    }
}

struct PhotoPage: Decodable {
    let total: Int
    let page: Int
    let pages: Int
    let photos: [Photo]
}

struct Photo: Decodable, Identifiable, Hashable {
    let id: Int
    let webName: String?
    let takenAt: String?
    let width: Int?
    let height: Int?
    let country: String?
    let place: String?
    let camera: String?
    let title: String?
    let kind: String?
    let durationS: Int?
    let rev: Int?

    var isVideo: Bool { kind == "video" }

    /// How tall the card gets in the grid. With no measurements: a square.
    var ratio: CGFloat {
        guard let w = width, let h = height, w > 0, h > 0 else { return 1 }
        return CGFloat(w) / CGFloat(h)
    }

    enum CodingKeys: String, CodingKey {
        case id, country, place, camera, title, kind, rev, width, height
        case webName = "web_name"
        case takenAt = "taken_at"
        case durationS = "duration_s"
    }
}

/// ⚠ NET `ShareLink` genannt: esou heescht e SwiftUI-Typ, an de Kompiler
/// would have taken one or the other in the same file.
struct ShareResult: Decodable, Identifiable {
    var id: String { url }

    let url: String
    let password: String
    let expiresAt: String?
    let n: Int?

    enum CodingKeys: String, CodingKey {
        case url, password, n
        case expiresAt = "expires_at"
    }
}

/// What already exists, so the upload form can offer it instead of asking
/// somebody to remember how they spelled a country last year.
struct Facets: Decodable {
    let years: [String]
    let countries: [String]
    let places: [String]
}

/// Where a video may be fetched from, and for how long that address is good.
struct VideoTicket: Decodable {
    let url: String
    let expiresIn: Int

    enum CodingKeys: String, CodingKey {
        case url
        case expiresIn = "expires_in"
    }
}

struct Batch: Decodable {
    let batch: String
}

/// ⚠ De Server seet `file_id`, net `id`.
struct NewFile: Decodable {
    let fileID: Int
    let ext: String?

    enum CodingKeys: String, CodingKey {
        case fileID = "file_id"
        case ext
    }
}
