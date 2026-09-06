//  The albums, and the photographs in them.

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
                    RemoteImage(id: cover, width: 400)
                } else {
                    Theme.groundWarm
                }
            }
            .frame(height: 118)
            .clipped()
            .clipShape(RoundedRectangle(cornerRadius: 4))

            Text(album.title)
                .font(.system(.callout, design: .serif))
                .foregroundStyle(Theme.ink)
                .lineLimit(2)
            Text("\(album.year) · \(album.n) photograph\(album.n == 1 ? "" : "s")")
                .font(.caption2.monospaced())
                .foregroundStyle(Theme.inkMute)
        }
    }
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
    @State private var sharing = false
    @State private var link: ShareResult?
    @State private var failed: String?

    private let cols = [GridItem(.adaptive(minimum: 110), spacing: 3)]

    var body: some View {
        ScrollView {
            LazyVGrid(columns: cols, spacing: 3) {
                ForEach(photos) { p in
                    NavigationLink(value: p) {
                        RemoteImage(id: p.id, width: 400)
                            .frame(height: 110)
                            .clipped()
                    }
                    .buttonStyle(.plain)
                    .onAppear {
                        // ⚠ The next page is loaded when its LAST image
                        //   appears -- not at a scroll offset. That way a fast
                        //   swipe does not fire the same request twice.
                        if p.id == photos.last?.id, page < pages, !loading {
                            Task { await load(page: page + 1) }
                        }
                    }
                }
            }
            .padding(3)
            if loading { ProgressView().tint(Theme.safelight).padding() }
            if let failed { Text(failed).foregroundStyle(.red).padding() }
        }
        .background(Theme.ground)
        .navigationTitle(album?.title ?? "Search")
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(for: Photo.self) { p in
            PhotoView(photos: photos, start: p)
        }
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
        }
        .sheet(item: $link) { l in ShareSheetView(link: l) }
        .task { if photos.isEmpty { await load(page: 1) } }
    }

    private func load(page wanted: Int) async {
        loading = true
        defer { loading = false }
        do {
            let res = try await state.api.photos(album: album, page: wanted, query: query)
            if wanted == 1 { photos = res.photos } else { photos += res.photos }
            page = res.page; pages = res.pages; total = res.total
            failed = nil
        } catch {
            failed = error.localizedDescription
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
