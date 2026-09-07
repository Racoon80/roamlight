//  The albums, and the photographs in them.

import PhotosUI
import SwiftUI

struct AlbumsView: View {
    @EnvironmentObject var state: AppState
    @State private var albums: [Album] = []
    @State private var loading = true
    @State private var failed: String?

    private let cols = [GridItem(.adaptive(minimum: 150), spacing: 12)]

    var body: some View {
        ScrollView {
            if loading {
                ProgressView().tint(Theme.safelight).padding(.top, 60)
            } else if let failed {
                Text(failed).foregroundStyle(.red).padding()
            } else if albums.isEmpty {
                Text("Nothing here yet.").foregroundStyle(Theme.inkMute).padding(.top, 60)
            } else {
                LazyVGrid(columns: cols, spacing: 12) {
                    ForEach(albums) { a in
                        NavigationLink(value: a) { AlbumCard(album: a) }
                            .buttonStyle(.plain)
                    }
                }
                .padding(12)
            }
        }
        .background(Theme.ground)
        .navigationTitle("Albums")
        .navigationDestination(for: Album.self) { PhotosView(album: $0) }
        .refreshable { await load() }
        .task { await load() }
    }

    private func load() async {
        do {
            albums = try await state.api.albums()
            failed = nil
        } catch {
            failed = error.localizedDescription
        }
        loading = false
    }
}

struct AlbumCard: View {
    let album: Album

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ZStack {
                if let cover = album.cover {
                    RemoteImage(id: cover, width: 400, rev: album.coverRev ?? 0)
                } else {
                    Theme.groundWarm
                }
            }
            // ⚠ The WIDTH has to be pinned too, not only the height.
            //   `RemoteImage` fills, so for a landscape photograph it hands back
            //   a view wider than its column -- the card then covers the one
            //   beside it and the grid looks as if it had no spacing at all,
            //   titles running into each other. `maxWidth: .infinity` makes the
            //   frame take exactly the column width; `.clipped()` cuts the rest.
            .frame(maxWidth: .infinity, minHeight: 118, maxHeight: 118)
            .clipped()
            .clipShape(RoundedRectangle(cornerRadius: 4))

            // ⚠ TWO lines, always -- also when the title needs only one.
            //   Without `reservesSpace` a short title makes the card shorter,
            //   the line underneath climbs up, and the row goes ragged: the
            //   counts in one row then sit at three different heights.
            Text(album.title)
                .font(.system(.callout, design: .serif))
                .foregroundStyle(Theme.ink)
                .lineLimit(2, reservesSpace: true)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
            // ⚠ `\(album.n)` inside a Text is a LocalizedStringKey, and that
            //   formats a number for the reader's language -- 1025 comes out as
            //   "1.025" here. Fine for a count, but it must not be mistaken for
            //   a decimal, so the word follows right after it.
            Text("\(album.year) · \(album.n) photograph\(album.n == 1 ? "" : "s")")
                .font(.caption2.monospaced())
                .foregroundStyle(Theme.inkMute)
                .lineLimit(1)
        }
        // The card fills its cell from the top, so a row of cards lines up
        // whatever the titles do.
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

/// The mark on a video in the grid: the triangle, and how long it runs.
@ViewBuilder
func videoMark(_ p: Photo) -> some View {
    HStack(spacing: 3) {
        Image(systemName: "play.fill").font(.system(size: 8))
        if let s = p.durationS, s > 0 {
            Text("\(s / 60):\(String(format: "%02d", s % 60))")
                .font(.system(size: 9, design: .monospaced))
        }
    }
    .foregroundStyle(.white)
    .padding(.horizontal, 4)
    .padding(.vertical, 2)
    .background(.black.opacity(0.55), in: Capsule())
    .padding(4)
}

/// Which albums have already shown their opening, for as long as the app runs.
///
/// ⚠ Not `@State`: the view is thrown away and rebuilt every time you walk into
///   the album, and then it would play again. Not stored on disk either — a new
///   day may as well start with the journey again.
final class Played {
    static let shared = Played()
    private var seen = Set<String>()
    func contains(_ id: String) -> Bool { seen.contains(id) }
    func insert(_ id: String) { seen.insert(id) }
}

// MARK: - D'Fotoen an engem Album

struct PhotosView: View {
    let album: Album?
    var query: String? = nil

    @EnvironmentObject var state: AppState
    @State private var photos: [Photo] = []
    @State private var page = 1
    @State private var pages = 1
    @State private var total = 0
    @State private var loading = false
    // ⚠ Which pages have been CLAIMED, not which have arrived. `loading` alone
    //   is not enough: `.onAppear` can fire several times for the same cell
    //   before the first Task has even started, all of them see `loading ==
    //   false`, and the same page is fetched and appended twice. That is what
    //   made the album scroll for ever with the same photographs coming round
    //   again.
    @State private var claimed: Set<Int> = []
    @State private var sharing = false
    @State private var link: ShareResult?
    @State private var failed: String?

    // Adding photographs to THIS album. ⚠ Open to everyone who may look at it,
    // not only to a contributor -- that is what the web page does, and it is
    // the whole point of "add a few of mine to the family album".
    @State private var adding: [PhotosPickerItem] = []
    @State private var sending = false
    @State private var sent: String?

    // ⚠ Once per album, like the website. Kept for as long as the app runs, so
    //   walking in and out of the same album does not replay it every time --
    //   that is charming the first time and tiresome the third.
    @State private var journey: Journey?
    @State private var played = false

    private let cols = [GridItem(.adaptive(minimum: 110), spacing: 3)]

    var body: some View {
        ScrollView {
            LazyVGrid(columns: cols, spacing: 3) {
                ForEach(photos) { p in
                    NavigationLink(value: p) {
                        RemoteImage(id: p.id, width: 400, rev: p.rev ?? 0)
                            // Width pinned to the column -- see `AlbumCard`.
                            .frame(maxWidth: .infinity, minHeight: 110, maxHeight: 110)
                            .clipped()
                            // ⚠ A video has to look like a video. Without this
                            //   it is a photograph that does nothing when you
                            //   open it -- the poster frame and no way to tell.
                            .overlay(alignment: .bottomTrailing) {
                                if p.isVideo { videoMark(p) }
                            }
                    }
                    .buttonStyle(.plain)
                    .onAppear {
                        // ⚠ The next page is loaded when its LAST image
                        //   appears -- not at a scroll offset. That way a fast
                        //   swipe does not fire the same request twice.
                        guard p.id == photos.last?.id, page < pages else { return }
                        let next = page + 1
                        // The claim is made HERE, synchronously, and not inside
                        // the Task -- see `claimed` above.
                        guard claimed.insert(next).inserted else { return }
                        Task { await load(page: next) }
                    }
                }
            }
            .padding(3)
            if sending {
                HStack(spacing: 8) {
                    ProgressView()
                    Text("Sending \(adding.count) photograph\(adding.count == 1 ? "" : "s")…")
                        .font(.footnote).foregroundStyle(Theme.inkSoft)
                }.padding()
            }
            if let sent {
                Text(sent).font(.footnote).foregroundStyle(Theme.inkSoft).padding(.horizontal)
            }
            if loading { ProgressView().tint(Theme.safelight).padding() }
            if let failed { Text(failed).foregroundStyle(.red).padding() }
        }
        .background(Theme.ground)
        .navigationTitle(album?.title ?? "Search")
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(for: Photo.self) { p in
            PhotoView(photos: photos, start: p)
        }
        .overlay {
            if let journey {
                JourneyView(journey: journey) {
                    withAnimation(.easeOut(duration: 0.45)) { self.journey = nil }
                }
                .transition(.opacity)
            }
        }
        .task(id: album?.id) {
            // ⚠ Swallowed on purpose: an album with no journey, or a site that
            //   cannot look the place up, must still open. The opening is a
            //   gift, not a gate.
            guard let album, !Played.shared.contains(album.id) else { return }
            Played.shared.insert(album.id)
            journey = try? await state.api.journey(album: album)
        }
        // Pull down to look again -- for the impatient, and for when a
        // conversion took longer than the twenty tries above.
        .refreshable { await load(page: 1) }
        .toolbar {
            if album != nil, state.me?.may.share == true {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await makeLink() } } label: {
                        if sharing { ProgressView() } else {
                            Image(systemName: "square.and.arrow.up")
                        }
                    }
                    .disabled(sharing)
                }
            }
            // ⚠ No rights check here beyond being signed in: the server decides
            //   (404 if you may not see this album), and every account holder
            //   may add to an album they can see. Hiding it behind `may.upload`
            //   would take it away from exactly the people it is meant for.
            if album != nil {
                ToolbarItem(placement: .topBarTrailing) {
                    PhotosPicker(selection: $adding, matching: .any(of: [.images, .videos]),
                                 photoLibrary: .shared()) {
                        if sending { ProgressView() } else {
                            Image(systemName: "plus.circle")
                        }
                    }
                    .disabled(sending)
                }
            }
        }
        .onChange(of: adding) { _, items in
            guard !items.isEmpty else { return }
            Task { await send(items) }
        }
        .sheet(item: $link) { l in ShareSheetView(link: l) }
        .task { if photos.isEmpty { await load(page: 1) } }
    }

    private func load(page wanted: Int) async {
        loading = true
        defer { loading = false }
        do {
            let res = try await state.api.photos(album: album, page: wanted, query: query)
            if wanted == 1 {
                photos = res.photos
                claimed = [1]
            } else {
                // ⚠ Belt and braces: even with the claim above, never append a
                //   photograph that is already in the list. SwiftUI needs the
                //   ids in a ForEach to be unique -- with a duplicate it starts
                //   drawing the wrong cells, which is exactly what "the same
                //   photographs keep coming" looks like.
                let known = Set(photos.map(\.id))
                photos += res.photos.filter { !known.contains($0.id) }
            }
            page = res.page; pages = res.pages; total = res.total
            failed = nil
        } catch {
            // Give the page back, so a scroll can try it again.
            claimed.remove(wanted)
            failed = error.localizedDescription
        }
    }

    /// Send the picked photographs into THIS album, one after another.
    ///
    /// ⚠ One at a time and not in one batch: the server checks and converts
    ///   each file, and a batch would only report at the very end which of them
    ///   it refused. This way a failure names the file.
    private func send(_ items: [PhotosPickerItem]) async {
        guard let album else { return }
        sending = true
        sent = nil
        failed = nil
        defer { sending = false; adding = [] }

        var done = 0
        for (i, item) in items.enumerated() {
            do {
                guard let data = try await item.loadTransferable(type: Data.self) else { continue }
                let ext = item.supportedContentTypes.first?.preferredFilenameExtension ?? "jpg"
                try await state.api.contribute(album: album,
                                               name: "IMG_\(i + 1).\(ext)", data: data)
                done += 1
            } catch {
                failed = error.localizedDescription
                break
            }
        }
        if done > 0 {
            // ⚠ One reload is not enough, and that is not a race -- it is how
            //   the site works. A photograph is stored, then CONVERTED, and it
            //   only counts as being on the site once that is finished
            //   (`state='ok'`). Ask straight away and the album comes back
            //   without it, which is why it seemed to arrive only after
            //   leaving the album and coming back in.
            //
            //   So: say what is happening, and keep looking until it is there.
            let before = photos.count
            sent = "\(done) photograph\(done == 1 ? "" : "s") sent — the site is converting."
            for _ in 0..<20 {
                await load(page: 1)
                if photos.count >= before + done { break }
                try? await Task.sleep(for: .seconds(1.5))
            }
            sent = photos.count >= before + done
                ? "\(done) photograph\(done == 1 ? "" : "s") added."
                : "\(done) sent. They will appear as soon as the site has converted them."
        }
    }

    private func makeLink() async {
        guard let album else { return }
        sharing = true
        defer { sharing = false }
        do { link = try await state.api.share(album: album) }
        catch { failed = error.localizedDescription }
    }
}

// MARK: - Sichen

struct SearchView: View {
    @EnvironmentObject var state: AppState
    @State private var text = ""
    @State private var run = ""

    var body: some View {
        PhotosView(album: nil, query: run.isEmpty ? nil : run)
            .id(run)                       // nei sichen = nei lueden
            .searchable(text: $text, prompt: "Date, place, camera, name, tag")
            .onSubmit(of: .search) { run = text }
    }
}
