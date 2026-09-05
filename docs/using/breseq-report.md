# Reading breseq's own report

breseq writes a full HTML report for every sample it analyzes: the mutation index, a summary of
the run, and an evidence page per call showing the read pileup it was called from. MutInt keeps
that report and shows it to you inside the application.

## Getting to it

Open a sample's mutations (**Mutations** in the sidebar, then pick the sample). Above the table
there are two links, **breseq report** and **summary**. They open the report inside MutInt, with
the sidebar still there and a way back.

From an evidence link in the report's own table you reach the read pileup for that call — the
thing that answers "why does breseq think this is a mutation".

## Which samples have one

Samples imported as **breseq folders**, from the Add Data page or from a run launched in
MutInt. The report is part of the folder, so it arrives with the data.

Samples imported as bare `.gd` files or from VCF **have no report** — those formats carry
mutations and nothing else — and the links are simply absent rather than broken.

Samples imported before this feature existed also have none: the report was discarded at the
time and there is nothing to recover. Re-importing the folder brings it.

## Opening it on its own

Each report page has an **open on its own** link, which gives you the raw breseq page in a new
tab — useful for a wide table, or for printing.

## A note on how it is shown

The report is displayed in a sandboxed frame. That is not cosmetic: breseq builds those pages
out of things that came from your data — sample names, read filenames, gene names out of the
reference — and MutInt treats any of it as untrusted. The sandbox means the report can draw
itself and run its own code, and cannot reach anything else in MutInt.

One visible consequence: the report is not part of the page around it. Your browser's Find
searches the frame you have clicked into, not both at once.
