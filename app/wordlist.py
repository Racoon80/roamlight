"""The words share-link passwords are built from.

726 short words. Four of them plus three digits is **48 bits** -- three words
and two digits would be about 36, which is not enough for something that sits
on the open internet behind nothing else.

They are chosen so that a password can be **read out over the phone**: short,
no accents, and no two words that sound alike.
"""
WORDS = """
apel appel arem auto bagage ball ballon bam bank bar barr bass bat bau
baum bee beem beer bei bel bell benz bild bir birn blat blatt blau blei
blitt blo bloh blot blum boch bock bod bomm bon boot borg bou boum branche
brei bri bridd brill brot brout bruch bua buch bud bur burg bus butt dach
dag dall damm dann dapp dar deck deel deich deif dell den depp desch dill
ding disch dokter dolch dorf douf draht drank dreck droch duch duerf dunn
eck eem eemer eeschel eesel eeuch ei eis eisen elch elo emmer ente erd
erdb esch esel eul faarf fabrik fach fad fal fall fann farb fass fee feld
fels fen ferm fest fett fiel figur film fink fisch flam flasch fleck
fleesch floss flou flug fluss folk form forst fra frang frau fries frosch
frucht fuch fuedem fues fuess gaard gaas gaascht gaass gabel gang gank
ganz garag garten gas gebai geck geescht geld gelo gems geo gewiicht gicht
gitt glas glo gold gras grenz grill gro grond gruef gruew guer haab haan
haart haass hall hals hamer hand hank hase haus heck heed heem heft heid
hell helm hemd henn herd herz himmel hiwwel hobby hoch hoer holz honeg
horn hous huel huet huf hund hunn hutt huus insel jaer jang jar joer jong
kaart kabel kaf kaffi kaiser kal kalb kall kamm kann kanu kap kapp kar
karp kart kast kat katz kaz keel keller kerb kessel kette kiel kier kilo
kin kino kirch kiss kitz kleed klemm klo kloster knapp knie knuet knuewel
koch koffer kolleg kopp korb korn kraft kranz krees kreid kroun kuch kugel
kuh kur kurv laart labo lach lack lad lag lai lamm lampe land lang lasch
latt laub lauf laus leed leeder leem leen leewen lehm leier lemmer lenz
lettre leyer licht lied lift liicht limm lind linn lipp lo loch loft lois
lok loop lorbeer los lou luef luft lupp mach magnet mais mal mand mann
mantel marx mass mast mat maul maus meck meer mehl mei mell mensch mer
mess metall meter miel milch mill min mir mist mitt moart mod mol mond
monk mont mool moos mopp motor mous muer muff muffel muller mund muschel
musek muster nach nagel name nascht natur neel nest netz neu nier nol nord
nout nues null nuss ober obst ochs offen ohr oktav ol olav onkel orgel ort
ouer ouschter ozean pack paff pak palm panz papp par park pass patt pech
peffer pei pell pelz pen perl pesch peter pfad phas pier pilz pion plack
plang plaz plou plus pol pop port post pot preis puddel pull pump punkt
purt quai quell quiz rad raf rahm ram rand rank rap rar rat raum reck reel
reen reff regel reh reif reis rem ren rescht ribbel richt ricken riem ring
rip risch riss ritt rock rod roll rond rou rous rub rucksak rud ruf ruh
rull rum rund saach saal sab saf saft sag sal salz sam sand sarg satz sau
saul schaf schall scham schank schar schat schaum scheer schell schierm
schif schild schlag schlass schleif schloss schlouss schmier schnee schock
schoss schoul schrank schreck schrift schuel schuh schull schutz schwan
schweess see seel segel seil sekt sen sess seuz siberg sicht sied silber
sinn sirop sitz skat ski sock sohn sonn sos spaass spak spalt span spann
spar spatz specht speck spiel spill spitz sport spott spuer staat stad
stal stamm stand stang stark start statt staub steen stell stern stief
stiel stil stir stock stoff stolz storm strand streck strofe strom stroum
stuck stuff stull sturm sud summ sun tabel tak taks tal tank tapp tasch
tass tat tau team teich teil teller tempo tenn text thron tick tier tipp
tir tisch titel tour towa traum tref tri trib trick trip trupp tuch turm
ubunn uecht uerdnung uewen ufank ur ursch vill vio virus vitt vogel volk
wal wald wall wand wank war ward wark warm wart wasch wat wee weeg wei
weid wein weis welt wenn werk wes west wetter wichtel wier wiese wiess
wiff wild wille wind wink winkel wirt wisch wiss witz wolf wolk wonn wort
wuel wull wurm wuurz zaan zaft zang zap zeen zeg zeil zeit zell zelt zent
zess zeug zich zil zimmer zinn zopp zuch zuck zuel zwang zweck zwerg zwig
""".split()
