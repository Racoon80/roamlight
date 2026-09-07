//  D'Verbindung zum Site. Alles, wat iwwer d'Netz geet, steet hei.
//
//  ⚠ One header does the signing in: `Authorization: Bearer fam_…`. The server
//    (app/devices.py) checks it, and the proxy then sets the same identity
//    headers as for a browser -- so exactly the same rights apply in the app as
//    on the site. No cookie, no session.

import Foundation

enum APIError: LocalizedError {
    case notConnected
    case http(Int, String)
    case badResponse

    var errorDescription: String? {
        switch self {
        case .notConnected: return "This device is not connected yet."
        case .badResponse:  return "The site sent something unexpected."
        case .http(401, _): return "This device was taken off the list. Connect it again."
        case .http(let c, let m): return m.isEmpty ? "The site said \(c)." : m
        }
    }
}

/// Where the site lives. Kept in the settings, because a family moves house
/// now and then -- and a hard-coded address would mean a new app.
enum Site {
    /// ⚠ There is no built-in address, and that is deliberate: the site is
    ///   somebody's own machine, and a hard-coded one would send this app's
    ///   requests to a stranger. It is learned from the QR code when pairing.
    ///
    /// ⚠ The fallback is `https://localhost` and not an empty string: an empty
    ///   string is not a URL, and force-unwrapping it would crash the app on
    ///   the very first screen, before anybody could pair it.
    static var url: URL {
        URL(string: UserDefaults.standard.string(forKey: "site") ?? "")
            ?? URL(string: "https://localhost")!
    }

    static func set(_ s: String) {
        UserDefaults.standard.set(s, forKey: "site")
    }
}

struct API {
    var token: String?

    // MARK: - Ufroen

    private func request(_ path: String, method: String = "GET",
                         body: Data? = nil, json: Bool = true) throws -> URLRequest {
        guard let url = URL(string: path, relativeTo: Site.url) else {
            throw APIError.badResponse
        }
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.httpBody = body
        if json, body != nil { r.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if let token { r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        r.timeoutInterval = 30
        return r
    }

    private func run(_ r: URLRequest) async throws -> Data {
        let (data, resp) = try await URLSession.shared.data(for: r)
        guard let http = resp as? HTTPURLResponse else { throw APIError.badResponse }
        guard (200..<300).contains(http.statusCode) else {
            // The server sends {"detail": "..."} -- that is the sentence shown
            // on an error. If there is none, the status code stays.
            var msg = ""
            if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let d = obj["detail"] as? String { msg = d }
            throw APIError.http(http.statusCode, msg)
        }
        return data
    }

    private func get<T: Decodable>(_ path: String, as: T.Type) async throws -> T {
        let data = try await run(try request(path))
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw APIError.badResponse }
    }

    private func post<T: Decodable>(_ path: String, _ body: [String: Any],
                                    as: T.Type) async throws -> T {
        let data = try await run(try request(
            path, method: "POST", body: try JSONSerialization.data(withJSONObject: body)))
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw APIError.badResponse }
    }

    // MARK: - Umellen

    /// Trade the pairing code for a token. This is the only request that
    /// ouni Token geet.
    static func pair(code: String, name: String, site: String) async throws -> Pairing {
        Site.set(site)
        let api = API(token: nil)
        return try await api.post("/api/app/pair", ["code": code, "name": name],
                                  as: Pairing.self)
    }

    /// Sign in with a name and a password.
    ///
    /// ⚠ The other way in. A pairing code needs a second machine -- you open
    ///   the site on a computer and type the code within five minutes. That is
    ///   fine for a family who already has it open, and no way in at all for
    ///   somebody holding only a phone. The site only offers this where it has
    ///   local accounts; behind an identity proxy it answers 404, and then
    ///   pairing is the road.
    static func signIn(site: String, user: String, password: String,
                       name: String) async throws -> Pairing {
        let had = Site.url
        Site.set(site)
        do {
            let api = API(token: nil)
            return try await api.post("/api/app/login",
                                      ["username": user, "password": password, "name": name],
                                      as: Pairing.self)
        } catch {
            // Put the old address back -- see the note on `pair`.
            Site.set(had.absoluteString == "https://localhost" ? "" : had.absoluteString)
            throw error
        }
    }

    func me() async throws -> Me { try await get("/api/app/me", as: Me.self) }

    // MARK: - Kucken

    func albums() async throws -> [Album] {
        try await get("/api/albums", as: AlbumList.self).albums
    }

    /// Add one photograph to an album that already exists.
    ///
    /// ⚠ This is the SAME route the web page uses
    /// (`POST /y/<year>/<country>/<event>/contribute`), and it is open to every
    /// registered person who may look at that album -- not only to a
    /// contributor. It goes live, with no approval; the checks (type from the
    /// content, virus scan) run on the server either way.
    ///
    /// ⚠ multipart/form-data with the field name `file`, because that is what
    ///   the server reads. A JSON body would be silently ignored.
    func contribute(album: Album, name: String, data: Data) async throws {
        let boundary = "roamlight.\(UUID().uuidString)"
        var body = Data()
        func put(_ s: String) { body.append(s.data(using: .utf8)!) }
        put("--\(boundary)\r\n")
        put("Content-Disposition: form-data; name=\"file\"; filename=\"\(name)\"\r\n")
        put("Content-Type: application/octet-stream\r\n\r\n")
        body.append(data)
        put("\r\n--\(boundary)--\r\n")

        let path = "/y/\(esc(album.year))/\(esc(album.country))/\(esc(album.event))/contribute"
        var r = try request(path, method: "POST", body: body, json: false)
        r.setValue("multipart/form-data; boundary=\(boundary)",
                   forHTTPHeaderField: "Content-Type")
        // A photograph off a phone is several MB, and a mobile network is not
        // the wifi at home.
        r.timeoutInterval = 300
        _ = try await run(r)
    }

    /// ⚠ Every part of the path has to be escaped on its own: an album called
    ///   "Ostend / Belgium" would otherwise become two path segments.
    private func esc(_ s: String) -> String {
        s.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(
            CharacterSet(charactersIn: "-._~"))) ?? s
    }

    /// The values that already exist -- viewer-scoped, like everything else.
    func facets() async throws -> Facets {
        try await get("/api/facets", as: Facets.self)
    }

    func photos(album: Album?, page: Int = 1, query: String? = nil) async throws -> PhotoPage {
        var q = URLComponents()
        q.queryItems = [URLQueryItem(name: "page", value: String(page))]
        if let a = album {
            q.queryItems! += [URLQueryItem(name: "year", value: a.year),
                              URLQueryItem(name: "country", value: a.country),
                              URLQueryItem(name: "event", value: a.event)]
        }
        if let query, !query.isEmpty { q.queryItems! += [URLQueryItem(name: "q", value: query)] }
        // ⚠ `percentEncodedQuery`, NOT `string`. `URLComponents.string` already
        //   carries the "?" -- putting another one in front made
        //   `/api/photos??page=2&year=…`, and then the first parameter is
        //   called "?page", not "page". So the page number never arrived, the
        //   server used its default of 1, and every album stopped at sixty
        //   photographs. (Earlier, before the list was de-duplicated, the same
        //   fault showed up as the first sixty coming round again and again.)
        return try await get("/api/photos?\(q.percentEncodedQuery ?? "")",
                             as: PhotoPage.self)
    }

    /// The address of an image. ⚠ WebP and not AVIF: iOS can do both, but the
    /// server computes AVIF at effort 2 -- WebP arrives sooner and far
    /// manner un.
    func imageURL(_ id: Int, width: Int, rev: Int = 0) -> URL? {
        URL(string: "/photos/\(id)/\(width).webp?v=\(rev)", relativeTo: Site.url)
    }

    /// Load an image. Has to be done by hand, because `AsyncImage` cannot set a header.
    ///
    /// ⚠ `?v=<rev>` is not decoration. The answers carry
    ///   `Cache-Control: private, max-age=31536000, immutable`, so without a
    ///   changing address a photograph that has been turned on the site stays
    ///   the old way round for a year.
    func image(_ id: Int, width: Int, rev: Int = 0) async throws -> Data {
        try await run(try request("/photos/\(id)/\(width).webp?v=\(rev)"))
    }

    // MARK: - Bescheed soen

    /// Tell the site where this phone can be reached.
    ///
    /// ⚠ This is NOT the token that lets the app read the library -- that one
    ///   lives in the keychain and is revoked when a phone is lost. This is
    ///   only an address for a one-line notice, and the site keeps the two
    ///   apart on purpose.
    func registerForNotices(token: String, name: String) async throws {
        _ = try await run(try request("/api/notify/register", method: "POST",
            body: try JSONSerialization.data(withJSONObject: [
                "kind": "apns", "token": token, "name": name])))
    }

    func forgetNotices(token: String) async throws {
        _ = try await run(try request("/api/notify/unregister", method: "POST",
            body: try JSONSerialization.data(withJSONObject: [
                "kind": "apns", "token": token])))
    }

    /// What the site can do about notices, and which phones it knows.
    func noticeState() async throws -> NoticeState {
        try await get("/api/notify", as: NoticeState.self)
    }

    // MARK: - D'Rees an d'Kaart

    /// The opening animation for an album. `nil` when the place cannot be
    /// located — then there is simply nothing to play.
    func journey(album: Album) async throws -> Journey? {
        var q = URLComponents()
        q.queryItems = [URLQueryItem(name: "year", value: album.year),
                        URLQueryItem(name: "country", value: album.country),
                        URLQueryItem(name: "event", value: album.event)]
        let j: Journey = try await get("/api/albums/journey?\(q.percentEncodedQuery ?? "")",
                                       as: Journey.self)
        return j.isEmpty ? nil : j
    }

    /// One map tile, THROUGH the site.
    ///
    /// ⚠ Not from openstreetmap.org directly, which is what the website does.
    ///   A phone asking for tiles says roughly where the album is, to somebody
    ///   who is not the family. The site already has the proxy and caches
    ///   them (app/tiles.py) — so the app uses it.
    func tile(z: Int, x: Int, y: Int) async throws -> Data {
        try await run(try request("/tiles/\(z)/\(x)/\(y).png"))
    }

    // MARK: - Video

    /// The address for the video, with its own proof in it.
    ///
    /// ⚠ Why not simply `/photos/<id>/video.mp4` with the token in a header:
    ///   `AVPlayer` does not send headers you give it. Apple has an option for
    ///   it that is not in the documentation, and a private key in a shipped
    ///   app is how the app stops working one release later. So the server
    ///   signs the one address instead -- fifteen minutes, that photograph
    ///   only, this person only.
    func videoURL(_ id: Int) async throws -> URL {
        let t: VideoTicket = try await get("/api/photo/\(id)/video", as: VideoTicket.self)
        guard let u = URL(string: t.url, relativeTo: Site.url) else {
            throw APIError.badResponse
        }
        return u
    }

    // MARK: - Deelen

    func share(album: Album, days: Int = 14) async throws -> ShareResult {
        try await post("/api/albums/share",
                       ["year": album.year, "country": album.country,
                        "event": album.event, "days": days, "allow_download": true],
                       as: ShareResult.self)
    }

    // MARK: - Eroplueden
    //
    // ⚠ In chunks, and not in one piece: a photograph off an iPhone is 5 MB
    //   before you know it, and a mobile network is not the wifi at home. The
    //   server already has the path (upload/batch -> file -> chunk -> done).

    static let chunk = 512 * 1024

    func newBatch() async throws -> String {
        try await post("/api/upload/batch", [:], as: Batch.self).batch
    }

    func addFile(batch: String, name: String, size: Int) async throws -> Int {
        try await post("/api/upload/\(batch)/file", ["name": name, "size": size],
                       as: NewFile.self).fileID
    }

    func sendChunk(batch: String, file: Int, offset: Int, data: Data) async throws {
        var r = try request("/api/upload/\(batch)/file/\(file)/chunk?offset=\(offset)",
                            method: "PUT", body: data, json: false)
        r.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        _ = try await run(r)
    }

    func finishFile(batch: String, file: Int) async throws {
        _ = try await run(try request("/api/upload/\(batch)/file/\(file)/done", method: "POST",
                                      body: Data("{}".utf8)))
    }

    func commit(batch: String, year: String, country: String,
                event: String, place: String) async throws {
        _ = try await run(try request(
            "/api/upload/\(batch)/commit", method: "POST",
            body: try JSONSerialization.data(withJSONObject: [
                "year": year, "country": country, "event": event, "place": place])))
    }
}
