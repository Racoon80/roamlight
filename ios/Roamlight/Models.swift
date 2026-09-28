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
    /// May this person edit the album as a whole? Said by the server for an
    /// album page; nil from an older site.
    let mayEdit: Bool?

    enum CodingKeys: String, CodingKey {
        case total, page, pages, photos
        case mayEdit = "may_edit"
    }
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
    /// May THIS person take it off the site? Said by the server (the same rule
    /// it enforces). `nil` from an older site -- then the button shows and the
    /// server decides.
    let mayRemove: Bool?

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
        case mayRemove = "may_remove"
    }
}

/// What "Edit album" is filled in with (`/api/albums/settings`).
struct AlbumSettings: Decodable {
    let year: String
    let country: String
    let event: String
    let place: String
    let journey: JourneyForm
    let transports: [String]
    let audience: [String]
    let people: [Choice]
    let groups: [Choice]
    let isAdmin: Bool

    struct JourneyForm: Decodable {
        let departure: String
        let transport: String
        let legs: [Leg]
        let multi: Bool
        struct Leg: Decodable {
            let transport: String
            let name: String
        }
    }

    struct Choice: Decodable, Identifiable, Hashable {
        let principal: String
        let name: String
        var id: String { principal }
    }

    enum CodingKeys: String, CodingKey {
        case year, country, event, place, journey, transports, audience, people, groups
        case isAdmin = "is_admin"
    }
}

/// The answer to renaming an album.
struct Edited: Decodable {
    let photos: Int
}

/// Which ways in a site has (`/api/app/ways`).
struct Ways: Decodable {
    let password: Bool
    let sso: Bool
}

/// The answer to taking something off the site.
struct Removed: Decodable {
    let removed: Int
    let failed: [Failure]

    struct Failure: Decodable {
        let id: Int
        let error: String
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

/// The opening animation of an album: where it started, and how it got there.
///
/// ⚠ EVERY album that can be located has one, even when nobody set anything:
///   then it is the stylised hop from home. The server decides that
///   (`journey.get_journey`), not the app.
/// What the site can do about notices, and which phones it knows.
struct NoticeState: Decodable {
    let devices: [Device]
    let apns: Bool
    let fcm: Bool

    struct Device: Decodable {
        let kind: String
        let name: String
        let lastOk: String?

        enum CodingKeys: String, CodingKey {
            case kind, name
            case lastOk = "last_ok"
        }
    }
}

struct Journey: Decodable {
    let departure: String?
    let from: [Double]
    let legs: [Leg]

    struct Leg: Decodable {
        let to: [Double]
        let transport: String
        /// The real road, for a car or a bus. `nil` for a plane — then it is
        /// drawn as an arc, the way a flight is drawn on paper.
        let route: [[Double]]?
        let name: String?
    }

    /// ⚠ The server answers in two shapes: a chain of `legs`, or a single hop
    ///   with `to`/`transport` at the top. One shape here, decided once.
    enum CodingKeys: String, CodingKey {
        case departure, from, legs, to, transport, route
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        departure = try? c.decode(String.self, forKey: .departure)
        from = (try? c.decode([Double].self, forKey: .from)) ?? []
        if let l = try? c.decode([Leg].self, forKey: .legs) {
            legs = l
        } else if let to = try? c.decode([Double].self, forKey: .to) {
            legs = [Leg(to: to,
                        transport: (try? c.decode(String.self, forKey: .transport)) ?? "car",
                        route: try? c.decode([[Double]].self, forKey: .route),
                        name: nil)]
        } else {
            legs = []
        }
    }

    var isEmpty: Bool { from.count < 2 || legs.isEmpty }
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

/// How far the filing of a batch has got.
///
/// ⚠ The commit answers AT ONCE and the site files the photographs away in its
///   own time -- so this is what says whether it is finished. Waiting for the
///   commit itself is what used to fail: 247 photographs take the site about
///   twelve minutes, and nothing in between (nginx, Cloudflare, a sleeping
///   phone) waits that long.
struct UploadStatus: Decodable {
    /// `waiting` (nothing asked for yet), `working`, `done`.
    let state: String
    let total: Int
    let stored: [Entry]
    let skipped: [Entry]
    let failed: [Entry]

    var isDone: Bool { state == "done" }
    var settled: Int { stored.count + skipped.count + failed.count }

    struct Entry: Decodable {
        let file: String?
        /// Only on `failed`.
        let error: String?
        /// Only on `skipped`.
        let reason: String?
    }
}
