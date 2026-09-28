//  The albums, and the photographs in them.

import PhotosUI
import SwiftUI

struct AlbumsView: View {
    @EnvironmentObject var state: AppState
    @State private var albums: [Album] = []
    @State private var loading = true
    @State private var failed: String?
    @State private var doomed: Album?
    @State private var problem: String?
    @State private var editing: Album?

    var body: some View {
        ScrollView {
            if loading {
                ProgressView().tint(Theme.safelight).padding(.top, 60)
            } else if let failed {
                Text(failed).foregroundStyle(.red).padding()
            } else if albums.isEmpty {
                Text("Nothing here yet.").foregroundStyle(Theme.inkMute).padding(.top, 60)
            } else {
                // The site's home page: "The albums", then one numbered row per
                // album -- plate, name, country, count, and the cover as a print.
                VStack(alignment: .leading, spacing: 0) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("The albums").font(Theme.display(30)).foregroundStyle(Theme.ink)
                        Spacer()
                        Text("\(albums.count) ALBUM\(albums.count == 1 ? "" : "S")")
                            .font(Theme.mono(10)).tracking(1.6).foregroundStyle(Theme.inkMute)
                    }
                    .padding(.bottom, 14)
                    Theme.rule.frame(height: 1)
                    LazyVStack(spacing: 0) {
                        ForEach(Array(albums.enumerated()), id: \.element.id) { i, a in
                            NavigationLink(value: a) { AlbumRow(album: a, number: i + 1) }
                                .buttonStyle(RowPress())
                                .contextMenu {
                                    // ⚠ "Edit" for anybody who may upload -- the
                                    //   screen itself says when the album is not
                                    //   theirs. "Delete" straight from the list
                                    //   only for an admin: for anybody else it
                                    //   would nearly always be a refusal.
                                    if state.mayRemove {
                                        Button { editing = a } label: {
                                            Label("Edit album", systemImage: "pencil")
                                        }
                                    }
                                    if state.me?.may.admin == true {
                                        Button(role: .destructive) { doomed = a } label: {
                                            Label("Delete album", systemImage: "trash")
                                        }
                                    }
                                }
                        }
                    }
                }
                .padding(.horizontal, 18)
                .padding(.top, 8)
            }
        }
        .background(Theme.ground)
        // "The albums" stands in the page, in Bodoni -- the bar stays empty.
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(for: Album.self) { PhotosView(album: $0) }
        .refreshable { await load() }
        .task { await load() }
        .confirmAlbumRemoval($doomed) { a in await remove(a) }
        .sheet(item: $editing) { a in
            EditAlbumView(album: a) { _ in Task { await load() } }
        }
        .alert("Not deleted", isPresented: Binding(get: { problem != nil },
                                                   set: { if !$0 { problem = nil } })) {
            Button("OK") {}
        } message: { Text(problem ?? "") }
    }

    private func remove(_ a: Album) async {
        do {
            let r = try await state.api.removeAlbum(a)
            if !r.failed.isEmpty {
                problem = "\(r.failed.count) of \(a.n) could not be deleted."
            }
        } catch {
            problem = error.localizedDescription
        }
        await load()
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

// MARK: - Ewechhuelen

/// Said BEFORE anything is deleted, not after.
///
/// ⚠ "Delete" means something different depending on where a photograph came
///   from, and the person pressing the button cannot see which it is. A
///   photograph uploaded from a phone exists ONLY on the site -- its source is
///   thrown away after conversion -- so it is gone for good. One from the
///   family library stays in the library; the site only forgets it (and does
///   not pick it up again on the next scan).
let removalWarning = "Photographs uploaded from a phone exist only on the site and are gone for good. Photographs from the family library stay in the library; only the site forgets them."

extension View {
    /// The one question asked before an album goes.
    func confirmAlbumRemoval(_ doomed: Binding<Album?>,
                             _ go: @escaping (Album) async -> Void) -> some View {
        confirmationDialog(
            "Delete “\(doomed.wrappedValue?.title ?? "")”?",
            isPresented: Binding(get: { doomed.wrappedValue != nil },
                                 set: { if !$0 { doomed.wrappedValue = nil } }),
            titleVisibility: .visible,
            presenting: doomed.wrappedValue
        ) { a in
            Button("Delete \(a.n) photograph\(a.n == 1 ? "" : "s")", role: .destructive) {
                Task { await go(a) }
            }
        } message: { _ in Text(removalWarning) }
    }

    /// The one question asked before a photograph goes.
    func confirmPhotoRemoval(_ doomed: Binding<Photo?>,
                             _ go: @escaping (Photo) async -> Void) -> some View {
        confirmationDialog(
            "Delete this \(doomed.wrappedValue?.isVideo == true ? "video" : "photograph")?",
            isPresented: Binding(get: { doomed.wrappedValue != nil },
                                 set: { if !$0 { doomed.wrappedValue = nil } }),
            titleVisibility: .visible,
            presenting: doomed.wrappedValue
        ) { p in
            Button("Delete", role: .destructive) { Task { await go(p) } }
        } message: { _ in Text(removalWarning) }
    }
}

/// One album, the way the site's home page lists it (`.index__row`).
struct AlbumRow: View {
    let album: Album
    let number: Int

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .center, spacing: 14) {
                VStack(alignment: .leading, spacing: 6) {
                    // ⚠ Above the name and not in a column beside it, as the
                    //   site has it on a wide screen: on a phone that column
                    //   cut the plate down to "PL. 0…".
                    Text("PL.\u{00A0}\(String(format: "%03d", number))")
                        .font(Theme.mono(10)).tracking(1.8)
                        .foregroundStyle(Theme.safelight)
                    Text(album.title.isEmpty ? album.event : album.title)
                        .font(Theme.display(25))
                        .foregroundStyle(Theme.ink)
                        .multilineTextAlignment(.leading)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                    // ⚠ `\(album.n)` straight into a Text is formatted for the
                    //   reader's language (1025 -> "1.025"). As a String it stays
                    //   a count.
                    // The year is already in the title the site sends.
                    Text("\(album.country.uppercased()) · \(String(album.n)) PLATE\(album.n == 1 ? "" : "S")")
                        .font(Theme.mono(9.5)).tracking(1.4)
                        .foregroundStyle(Theme.inkMute)
                        .lineLimit(1)
                }
                Spacer(minLength: 4)
                if let cover = album.cover {
                    // The cover as a print, turned a little -- the site shows it
                    // like that when the pointer passes over the row.
                    RemoteImage(id: cover, width: 400, rev: album.coverRev ?? 0)
                        .frame(width: 78, height: 54)
                        .clipped()
                        .padding(3)
                        .background(Theme.paper)
                        .rotationEffect(.degrees(-1.5))
                        .shadow(color: .black.opacity(0.55), radius: 8, y: 5)
                }
            }
            .padding(.vertical, 18)
            .contentShape(Rectangle())
            Theme.rule.frame(height: 1)
        }
    }
}

/// A row that answers a touch the way the site answers the pointer.
struct RowPress: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.leading, configuration.isPressed ? 10 : 0)
            .background(configuration.isPressed ? Theme.groundWarm : .clear)
            .animation(.easeOut(duration: 0.25), value: configuration.isPressed)
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
    @State private var doomedPhoto: Photo?
    @State private var editing = false
    @State private var problem: String?
    @Environment(\.dismiss) private var dismiss

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

    @State private var width: CGFloat = 0
    @State private var slideshow = false

    var body: some View {
        ScrollView {
            if let album { header(album) }
            // ⚠ The collage is laid out from the width, so it waits for it.
            //   A first pass with width 0 would make one enormous row.
            if width > 0 {
                Collage(photos: photos, width: max(0, width - 28)) { p in
                    NavigationLink(value: p) { Print(photo: p) }
                        .buttonStyle(PrintPress())
                        .contextMenu {
                            if state.mayRemove, p.mayRemove ?? true {
                                Button(role: .destructive) { doomedPhoto = p } label: {
                                    Label(p.isVideo ? "Delete video" : "Delete photograph",
                                          systemImage: "trash")
                                }
                            }
                        }
                } onLastRow: {
                    // ⚠ The next page is loaded when the LAST row appears --
                    //   not at a scroll offset. That way a fast swipe does not
                    //   fire the same request twice.
                    guard page < pages else { return }
                    let next = page + 1
                    // The claim is made HERE, synchronously, and not inside
                    // the Task -- see `claimed` above.
                    guard claimed.insert(next).inserted else { return }
                    Task { await load(page: next) }
                }
                // Room for the corners of a turned print at the screen's edge.
                .padding(.horizontal, 14)
                .padding(.top, 8)
                .padding(.bottom, 32)
            }
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
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        .background(Theme.ground)
        // ⚠ An album opened from a notice carries only its key -- there was no
        //   list to take a title from. Then the name of the event is the title,
        //   which is what it is made of anyway.
        // The album's title stands in the page itself, in Bodoni, as on the
        // site -- a second copy in the bar would only repeat it.
        .navigationTitle(album == nil ? "Search" : "")
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(for: Photo.self) { p in
            PhotoView(photos: photos, start: p) { gone in
                photos.removeAll { $0.id == gone.id }
                total = max(0, total - 1)
            }
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
            if !photos.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { slideshow = true } label: { Image(systemName: "play.rectangle") }
                        .accessibilityLabel("Slideshow")
                }
            }
            if album != nil, state.mayRemove {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { editing = true } label: { Image(systemName: "slider.horizontal.3") }
                        .accessibilityLabel("Edit album")
                }
            }
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
        .task {
            if photos.isEmpty { await load(page: 1) }
            #if DEBUG
            if Demo.env("ROAMLIGHT_DEMO_SLIDESHOW") != nil, album != nil { slideshow = true }
            #endif
        }
        .confirmPhotoRemoval($doomedPhoto) { p in await remove(p) }
        .fullScreenCover(isPresented: $slideshow) {
            SlideshowView(album: album, query: query, start: photos, total: total)
        }
        .sheet(isPresented: $editing) {
            if let album {
                EditAlbumView(album: album) { left in
                    // Renamed or deleted: this album is not in the list under
                    // this name any more -- back to the list, which reloads.
                    if left { dismiss() } else { Task { await load(page: 1) } }
                }
            }
        }
        // ⚠ An alert, not a line of text at the foot of the grid. The line was
        //   there on 28.09.2026 when the site refused two deletions -- and
        //   nobody saw it, so the photographs just seemed not to go.
        .alert("Not deleted", isPresented: Binding(get: { problem != nil },
                                                   set: { if !$0 { problem = nil } })) {
            Button("OK") {}
        } message: { Text(problem ?? "") }
    }

    private func remove(_ p: Photo) async {
        do {
            let r = try await state.api.removePhotos([p.id])
            if r.removed > 0 {
                photos.removeAll { $0.id == p.id }
                total = max(0, total - 1)
            } else {
                problem = r.failed.first?.error ?? "The site did not delete it."
            }
        } catch {
            problem = error.localizedDescription
        }
    }

    /// The site's album head: a plate with year and country, the title in
    /// Bodoni, and how many plates.
    private func header(_ a: Album) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("\(a.year) · \(a.country.uppercased())")
                .font(Theme.mono(10)).tracking(1.8).foregroundStyle(Theme.safelight)
            Text(a.title.isEmpty ? a.event : a.title)
                .font(Theme.display(34, relativeTo: .largeTitle))
                .foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            if total > 0 {
                Text("\(String(total)) PLATE\(total == 1 ? "" : "S")")
                    .font(Theme.mono(10)).tracking(1.6).foregroundStyle(Theme.inkMute)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 18)
        .padding(.top, 12)
        .padding(.bottom, 6)
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
            // ⚠ The TOTAL, not how many are on screen. `load(page: 1)` replaces
            //   the list with the first sixty; in an album with more than that,
            //   the count on screen FALLS when it reloads, and a new photograph
            //   sorts to the last page anyway. Comparing what is visible meant
            //   the wait always ran its full thirty seconds and then said the
            //   photographs had not arrived — while they were already there.
            let before = total
            sent = "\(done) photograph\(done == 1 ? "" : "s") sent — the site is converting."
            for _ in 0..<20 {
                await load(page: 1)
                if total >= before + done { break }
                try? await Task.sleep(for: .seconds(1.5))
            }
            sent = total >= before + done
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
