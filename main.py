import base64
import re
from io import BytesIO

import streamlit as st
from openai import OpenAI
from PIL import Image
from pychord import Chord as PyChord
from pychord.constants.qualities import DEFAULT_QUALITIES
from pychord.constants.scales import (FLATTED_SCALE, SCALE_VAL_DICT,
                                      SHARPED_SCALE)
from streamlit.components.v1 import html

DEFAULT_QUALITIES.extend([
    ("ø", (0, 3, 6, 10)),
    ("°", (0, 3, 6, 9)),
    ("Δ", (0, 4, 7, 11)),
    ("M13", (0, 4, 7, 11, 14, 21))
])


class Chord():
    def __init__(self, name, clean_chord=False) -> None:
        self.name = name.strip()
        print(self.name)
        if clean_chord:
            self.name = self.get_clean_chord()
        
    @property
    def latex_name(self):
        return self.name.replace("#", "\#").replace("b", "♭")

    @property
    def display_name(self):
        return self.name.replace("/", "|")
        
    def get_clean_chord(self):
        chord = self.name
        all_replacements = [("min", "m"), ("mi", "m"), ("-", "m"), ("maj", "M"), ("ma", "M"), ("MAJ", "M"), ("Mj", "M"), ("Maj", "M"), ("Ma", "M"), ("MA", "M"), ("♭", "b"), ("♯", "#"), ("6/9", "69"), ("°", "dim")]
    
        for a, b in all_replacements:
            chord = chord.replace(a, b)
        
        if chord.startswith("(") and chord.endswith(")"):
            chord = chord[1:-1]

        alt = re.findall(r'\(([^\)]*)\)', chord)
        alt = alt[0] if len(alt) > 0 else None
        pattern = r'([^\(\)]+)(\([^\)]*\))*'
        chord = re.match(pattern, chord).group(1)
        
        if (alt is not None) and (alt[0] in "b#") and (len(alt) == 2):
            chord += alt
        elif (alt is not None) and (alt.startswith("add")):
            chord += alt
        
        return chord
        
    def get_pychord(self):
        chord = self.get_clean_chord()
        
        try:
            return PyChord(chord)
        except Exception as e:
            print("EXC:" + str(e))
            return None
    
    def get_notes(self):
        if (chord := self.get_pychord()) is None:
            return []
        
        components = chord.components(visible=False)
        root = chord.root
        quality = chord.quality._quality

        scale = SCALE_VAL_DICT[root]
        if len(root) == 1:
            if any(c in quality for c in ["m", "ø", "°"]):
                scale = FLATTED_SCALE
            elif any(c in quality for c in ["M", "Δ"]):
                scale = SHARPED_SCALE
        return [scale[c%12] for c in components]

    def get_abc(self):
        notes = self.get_notes()
        
        list_notes = ["C", "D", "E", "F", "G", "A", "B"]
        mapping = {"#": "^", "b" : "_"}

        n_switch = 0
        idx_switch = []
        for i in range(len(notes)-1):
            if (list_notes.index(notes[i+1][0]) - list_notes.index(notes[i][0]) < 0):
                n_switch += 1
                idx_switch.append(i+1)
            
        abc_notes = []
        for i, note in enumerate(notes):
            if len(note) > 1:
                note = f"{mapping[note[1]]}{note[0]}"
            if n_switch == 2 and i < idx_switch[0]:
                note += ","
            elif (n_switch == 2 and i >= idx_switch[1]) or (n_switch == 1 and i >= idx_switch[0]):
                note = note.lower()
            abc_notes.append(note)
        
        return ''.join(abc_notes)


class ChordGroup():
    def __init__(self, chords=None, notes_per_line=4):
        self.chords = chords or []
        self.notes_per_line = notes_per_line
    
    def from_prediction(self, chords):
        self.chords = []
        for line in chords:
            line_group = []
            for measure in line:
                if ms := [Chord(c, True) for c in measure]:
                    line_group.append(ms)
            if len(line_group) > 0:
                self.chords.append(line_group)
        
    def to_text(self):
        return "\n".join([
            "  |  ".join(["  ".join(c.name for c in m) for m in line])
            for line in self.chords
        ])
        
    def from_text(self, text):
        self.chords = []
        for line in text.split("\n"):
            line_group = []
            for measure in line.split("|"):
                if ms := [Chord(c) for c in measure.split() if c != ""]:
                    line_group.append(ms)
            if line_group:
                self.chords.append(line_group)

    def to_abc(self, key="C"):
        abc = f"K:{key}\nL:4/4\n"
        flatten_chords = [c for line in self.chords for c in line]
        for i, chord_group in enumerate(flatten_chords):
            for c in chord_group:
                notes = c.get_abc()
                time = "" if len(chord_group) == 1 else f"1/{len(chord_group)}"
                abc += f'"{c.display_name}"[{notes}]{time} '
            abc += " | " if (i+1) % self.notes_per_line != 0 else "\n"
        return abc

    def to_grid(self):
        grid = []
        line = []
        flatten_chords = [c for line in self.chords for c in line]
        for i, chord_group in enumerate(flatten_chords):
            
            chord_group = [c.latex_name for c in chord_group]
            line.append(chord_group)
            if i % 4 == 3:
                grid.append(line)
                line = []
        if line:
            grid.append(line)
        return grid



client = OpenAI(api_key=st.secrets["openai_key"])


if "chords" not in st.session_state:
    st.session_state.key = "C"
    st.session_state.chords = ChordGroup()
    st.session_state.chords_txt = ""
    st.session_state.grid = []


# @st.cache_data
def predict(image):
    prompt = "Extract the key and all the chord symbols above the staff in a list. On the first line, write the key (eg K: Cm), then on the second line, write L:, then answer only with the lists of list of symbols [], one list per staff line, separating chord symbols with commas, separating lists with semicolon ';'. If there are more than one chord within one measure, do not separate them with a comma. Example:\nK: Cm\nL: [A, Bm, Cb-7];[D, E#, F7 D#]]\nHere F7 and D# are in the same measure."  

    encoded_image = base64.b64encode(image.getvalue()).decode('utf-8')
    
    response = client.chat.completions.create(
        model="gpt-4-vision-preview",
        messages=[
            {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{encoded_image}",
                    "detail": "high"
                },
                },
            ],
            }
        ],
        max_tokens=300,
    )

    return response.choices[0].message.content


def parse_prediction(prediction):
    lines = prediction.splitlines()
    for l in lines:
        if l.startswith("K:"):
            key = l.split(":")[1]
        elif l.startswith("L:"):
            chords = l.split(":")[1]
            
    if key == "" or chords == "":
        return None, None
    
    chords = re.sub(r'[\[\]]', '', chords)
    chords = [[[x for x in c.strip().split() if x != ""] for c in line.split(",") ] for line in chords.split(";")]

    return key, chords


def update():
    st.session_state.chords.from_text(st.session_state.chords_txt)
    st.session_state.abc_notation = st.session_state.chords.to_abc(key=st.session_state.key)
    st.session_state.grid = st.session_state.chords.to_grid()
 
st.title('Chords Extracter')
st.markdown("This app extracts the chords written as text from an image of a music sheet and displays them in a grid and in ABC notation.")

example_button = st.container()

with st.form(key='my_form'):
    file = st.file_uploader("Upload an image of a music sheet", type=["jpeg", "jpg", "png"])
    submit_button = st.form_submit_button(label='Submit')

if example_button.button("Try with an example image"):
    img = Image.open("example.jpg")
    file = BytesIO()
    img.save(file, format='jpeg')
    submit_button = True

if file is not None:
    st.image(file, caption='Uploaded Image', use_column_width=True)
    if submit_button:
        with st.spinner("Parsing image..."):
            key = chords = None
            while key is None or chords is None:
                out = predict(file)
                key, chords = parse_prediction(out)
                        
        st.session_state.key = key
        st.session_state.chords.from_prediction(chords)
        st.session_state.chords_txt = st.session_state.chords.to_text()
        st.session_state.abc_notation = st.session_state.chords.to_abc(key=st.session_state.key)
        st.session_state.grid = st.session_state.chords.to_grid()


st.text_area("Chords", height=200, key="chords_txt", on_change=update)

with st.expander("Details", False):
    st.text_area("ABC notation", height=200, key="abc_notation")
    st.write("For more details on ABC: http://anamnese.online.fr/site2/pageguide_abc.php")
    grid_size = st.selectbox("Grid size", ["\Large", "\huge", "\Huge"], format_func=lambda x: x[1:], key="grid_size")


if len(st.session_state.grid) > 0:
    st.latex(
        grid_size +
        r" \begin{array}{|c|c|c|c|} \hline " +
        r"\\ \hline ".join([" & ".join([" / ".join(r"\text{" + t + r"}" for t in c) for c in line]) for line in st.session_state.grid]) +
        r" \\ \hline \end{array}"
    )

    embed_code = f"""
    <script src="https://cdn.jsdelivr.net/npm/abcjs@6.2.3/dist/abcjs-basic-min.js"></script>
    <div id="paper"></div>
    <script type="text/javascript">
        function renderABC() {{
            var visualObj = ABCJS.renderAbc("paper", `%%stretchlast\n%%staffwidth 600\n{st.session_state.abc_notation}`, staffwidth=4000, stretchlast=true);
            visualObj[0].setUpAudio();
        }}
        
        function saveSvg() {{
            let svgElement = document.querySelector("#paper svg");
            svgElement.setAttribute("xmlns", "http://www.w3.org/2000/svg");
            var svgData = svgElement.outerHTML;
            var preface = '<?xml version="1.0" standalone="no"?>\\r\\n';
            var svgBlob = new Blob([preface, svgData], {{type:"image/svg+xml;charset=utf-8"}});
            var svgUrl = URL.createObjectURL(svgBlob);
            var downloadLink = document.createElement("a");
            downloadLink.href = svgUrl;
            downloadLink.download = "music_score.svg";
            downloadLink.click();
        }}

        function addDownloadButton() {{
            let btn = document.createElement("button");
            btn.textContent = "Download as SVG";
            btn.onclick = saveSvg;
            document.body.appendChild(btn);
        }}

        window.onload = () => {{
            renderABC();
            addDownloadButton();
        }};
    </script>
    """

    html(embed_code, height=650, scrolling=True)