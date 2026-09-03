"""A stored mutation still writes the GenomeDiff line it was read from.

`Mutation.gd_data` keeps breseq's record verbatim so `to_gd_line()` can hand it back to
`gdtools APPLY`. On PostgreSQL that column is `jsonb`, **which does not preserve key order**,
and `Record.__str__` writes the type's own fields in the schema's order and then whatever is
left in the mapping's order. So the trailing `key=value` fields can come back in a different
order than they went in.

**That is accepted rather than engineered around**, and the reasons are worth keeping beside
the test that pins what "accepted" means:

- The contract was never byte-fidelity. The parser returns `PreservedInt`/`PreservedFloat` so
  that `frequency=8.39314286e-01` round-trips through *it* unchanged -- and JSON has one
  number type, so storing it flattens the notation to `0.839314286`. `gd_data` has always
  been verbatim in content, not in bytes.
- The positional half of a record is unaffected: `TYPE_SPECIFIC_FIELDS` decides the order of
  everything that is positional, and `gdtools` parses the rest by name.
- Forcing the column to `json` instead of `jsonb` to keep the order would foreclose every
  jsonb operator and index for the life of the schema, to protect a property nothing reads --
  and it is the same shape as the vendored-backend subclass this work deleted.
- Sorting the trailing fields in `to_gd_line()` would be worse: it makes the output differ
  from the source file *deterministically* rather than incidentally, and puts a second opinion
  about field order beside `Record.__str__`, which is the package's own serializer.

So what is asserted here is **equality of records, not of strings**, and the docstring says so
because the obvious "improvement" to this test is to compare the two lines.
"""

from io import StringIO

from django.test import TestCase
from genomediff import GenomeDiff

from aledb_seq.models import Mutation

#: One of each shape that carries trailing key=value fields, which is where order can move.
LINES = [
    "SNP\t1\t2\tNC_000913\t3909807\tC\tgene_name=thrA\tgene_position=42\tfrequency=0.83",
    "DEL\t2\t.\tNC_000913\t4296380\t16229\trepeat_name=IS5\tgene_name=[insH1]–[flhD]",
    "MOB\t3\t.\tNC_000913\t16972\tIS186\t1\t6\tgene_name=mokC/nhaA\tfrequency=1",
    "AMP\t4\t.\tNC_000913\t3289962\t34\t2\tgene_name=rrlD",
]


def parse_one(line):
    document = GenomeDiff.read(StringIO("#=GENOME_DIFF\t1.0\n" + line + "\n"))
    return document.mutations[0]


class GdRoundTripTestCase(TestCase):

    def _stored(self, record):
        data = dict(record.attributes)
        data["type"] = record.type
        mutation = Mutation.objects.create(
            mutation_type=record.type,
            position=record.attributes.get("position", 1),
            reseq_reference=record.attributes.get("seq_id", ""),
            sequence_change="", gd_data=data)
        # Refetched, so what is asserted is what the database gave back rather than the dict
        # still in memory -- which is the whole point on a backend that reorders keys.
        return Mutation.objects.get(pk=mutation.pk)

    def test_every_field_survives_the_database(self):
        for line in LINES:
            with self.subTest(line=line.split("\t")[0]):
                original = parse_one(line)

                written = self._stored(original).to_gd_line()
                again = parse_one(written)

                self.assertEqual(original.type, again.type)
                self.assertEqual(original.attributes, again.attributes)

    def test_the_positional_fields_keep_their_order(self):
        """The half that is not a mapping. `TYPE_SPECIFIC_FIELDS` decides these, so they
        cannot move whatever the database does to key order -- and a MOB is the one to check,
        having four of them."""
        original = parse_one(LINES[2])

        written = self._stored(original).to_gd_line()

        fields = written.split("\t")
        self.assertEqual(["MOB", "NC_000913", "16972", "IS186", "1", "6"],
                         [fields[0]] + fields[3:8])

    def test_a_number_keeps_its_value_if_not_its_notation(self):
        """Stated rather than left to be discovered: JSON has one number type, so exponent
        notation does not survive storage. The value does, which is what APPLY reads."""
        original = parse_one(
            "SNP\t1\t.\tNC_000913\t100\tC\tfrequency=8.39314286e-01")

        again = parse_one(self._stored(original).to_gd_line())

        self.assertEqual(float(original.attributes["frequency"]),
                         float(again.attributes["frequency"]))
