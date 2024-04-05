import datetime
import textwrap
import pydantic
from pydantic import AfterValidator, Field, BaseModel, field_validator
from typing import Annotated, Generic, Literal, TypeVar
from typing import List

import pytest

import dspy
from dspy.functional import (
    predictor,
    cot,
    FunctionalModule,
    TypedPredictor,
    TypedChainOfThought,
)
from dspy.predict.predict import Predict
from dspy.primitives.example import Example
from dspy.teleprompt.bootstrap import BootstrapFewShot
from dspy.teleprompt.vanilla import LabeledFewShot
from dspy.utils import DummyBackend, DummyLM


def test_simple():
    @predictor
    def hard_question(topic: str) -> str:
        """Think of a hard factual question about a topic."""

    expected = "What is the speed of light?"
    lm = DummyLM([expected])
    with dspy.settings.context(lm=lm, backend=None):
        question = hard_question(topic="Physics")
        lm.inspect_history(n=2)

        assert question == expected


def test_simple_with_backend():
    @predictor
    def hard_question(topic: str) -> str:
        """Think of a hard factual question about a topic."""

    backend = DummyBackend(answers=[["What is the speed of light?"]])
    with dspy.settings.context(backend=backend, lm=None, cache=False):
        expected = "What is the speed of light?"

        question = hard_question(topic="Physics")

        assert question == expected


def test_list_output():
    @predictor
    def hard_questions(topics: List[str]) -> List[str]:
        pass

    expected = ["What is the speed of light?", "What is the speed of sound?"]
    lm = DummyLM(['{"value": ["What is the speed of light?", "What is the speed of sound?"]}'])
    with dspy.settings.context(lm=lm, backend=None):
        question = hard_questions(topics=["Physics", "Music"])
        lm.inspect_history(n=2)

        assert question == expected


def test_simple_type():
    class Question(pydantic.BaseModel):
        value: str

    @predictor
    def hard_question(topic: str) -> Question:
        """Think of a hard factual question about a topic."""

    expected = "What is the speed of light?"
    lm = DummyLM([f'{{"value": "{expected}"}}'])
    with dspy.settings.context(lm=lm, backend=None):
        question = hard_question(topic="Physics")

        assert isinstance(question, Question)
        assert question.value == expected


def test_simple_type_with_backend():
    class Question(pydantic.BaseModel):
        value: str

    @predictor
    def hard_question(topic: str) -> Question:
        """Think of a hard factual question about a topic."""

    expected = "What is the speed of light?"
    backend = DummyBackend(answers=[[f'{{"value": "{expected}"}}']])
    with dspy.settings.context(backend=backend, lm=None):
        question = hard_question(topic="Physics")

        assert isinstance(question, Question)
        assert question.value == expected


def test_simple_type_input():
    class Question(pydantic.BaseModel):
        value: str

    class Answer(pydantic.BaseModel):
        value: str

    @predictor
    def answer(question: Question) -> Answer:
        pass

    question = Question(value="What is the speed of light?")
    lm = DummyLM([f'{{"value": "3e8"}}'])
    with dspy.settings.context(lm=lm, backend=None):
        result = answer(question=question)

        assert result == Answer(value="3e8")


def test_simple_type_input_with_backend():
    class Question(pydantic.BaseModel):
        value: str

    class Answer(pydantic.BaseModel):
        value: str

    @predictor
    def answer(question: Question) -> Answer:
        pass

    question = Question(value="What is the speed of light?")

    backend = DummyBackend(answers=[[f'{{"value": "3e8"}}']])
    with dspy.settings.context(lm=None, backend=backend):
        result = answer(question=question)

        assert result == Answer(value="3e8")


def test_simple_class():
    class Answer(pydantic.BaseModel):
        value: float
        certainty: float
        comments: List[str] = pydantic.Field(description="At least two comments about the answer")

    class QA(FunctionalModule):
        @predictor
        def hard_question(self, topic: str) -> str:
            """Think of a hard factual question about a topic. It should be answerable with a number."""

        @cot
        def answer(self, question: Annotated[str, "Question to answer"]) -> Answer:
            pass

        def forward(self, **kwargs):
            question = self.hard_question(**kwargs)
            return (question, self.answer(question=question))

    expected = Answer(
        value=3e8,
        certainty=0.9,
        comments=["It is the speed of light", "It is a constant"],
    )

    lm = DummyLM(
        [
            "What is the speed of light?",
            "Some bad reasoning, 3e8 m/s.",
            "3e8",  # Bad answer 1
            "{...}",  # Model is asked to create an example
            "Some good reasoning...",
            expected.model_dump_json(),  # Good answer
        ]
    )

    with dspy.settings.context(lm=lm, backend=None):
        qa = QA()
        assert isinstance(qa, FunctionalModule)
        assert isinstance(qa.answer, dspy.Module)

        question, answer = qa(topic="Physics")

        print(qa.answer)

        assert question == "What is the speed of light?"
        assert answer == expected


def test_simple_class_with_backend():
    class Answer(pydantic.BaseModel):
        value: float
        certainty: float
        comments: List[str] = pydantic.Field(description="At least two comments about the answer")

    class QA(FunctionalModule):
        @predictor
        def hard_question(self, topic: str) -> str:
            """Think of a hard factual question about a topic. It should be answerable with a number."""

        @cot
        def answer(self, question: Annotated[str, "Question to answer"]) -> Answer:
            pass

        def forward(self, **kwargs):
            question = self.hard_question(**kwargs)
            return (question, self.answer(question=question))

    expected = Answer(
        value=3e8,
        certainty=0.9,
        comments=["It is the speed of light", "It is a constant"],
    )

    backend = DummyBackend(
        answers=[
            ["What is the speed of light?"],
            ["Some bad reasoning, 3e8 m/s\n\nAnswer: 3e8"],
            [  # Model is asked to create an example
                f"{...}",
            ],
            [f"Some good reasoning...\n\nAnswer: {expected.model_dump_json()}"],
        ]
    )
    with dspy.settings.context(backend=backend, lm=None, cache=False):
        qa = QA()
        assert isinstance(qa, FunctionalModule)
        assert isinstance(qa.answer, dspy.Module)

        question, answer = qa(topic="Physics")

        assert question == "What is the speed of light?"
        assert answer == expected


def test_simple_oop_with_backend():
    class Question(pydantic.BaseModel):
        value: str

    class MySignature(dspy.Signature):
        topic: str = dspy.InputField()
        output: Question = dspy.OutputField()

    # Run the signature
    program = TypedPredictor(MySignature)
    expected = "What is the speed of light?"
    backend = DummyBackend(answers=[[Question(value=expected).model_dump_json()]])
    with dspy.settings.context(backend=backend, lm=None, cache=False):
        question = program(topic="Physics").output

        assert isinstance(question, Question)
        assert question.value == expected


def test_equivalent_signatures():
    class ClassSignature(dspy.Signature):
        input: str = dspy.InputField()
        output: str = dspy.OutputField()

    @predictor
    def output(input: str) -> str:
        pass

    function_signature = output.predictor.signature

    simple_signature = dspy.Signature("input -> output")

    assert ClassSignature.equals(function_signature)
    assert ClassSignature.equals(simple_signature)


def test_named_params():
    class QA(FunctionalModule):
        @predictor
        def hard_question(self, topic: str) -> str:
            """Think of a hard factual question about a topic. It should be answerable with a number."""

        @cot
        def answer(self, question: str) -> str:
            pass

    qa = QA()
    named_predictors = list(qa.named_predictors())
    assert len(named_predictors) == 2
    names, _ = zip(*qa.named_predictors())
    assert set(names) == {
        "hard_question.predictor.predictor",
        "answer.predictor.predictor",
    }


def test_bootstrap_effectiveness_with_backend():
    class SimpleModule(FunctionalModule):
        @predictor
        def output(self, input: str) -> str:
            pass

        def forward(self, **kwargs):
            return self.output(**kwargs)

    def simple_metric(example, prediction, trace=None):
        return example.output == prediction.output

    examples = [
        ex.with_inputs("input")
        for ex in (
            Example(input="What is the color of the sky?", output="blue"),
            Example(
                input="What does the fox say?",
                output="Ring-ding-ding-ding-dingeringeding!",
            ),
        )
    ]
    trainset = [examples[0]]
    valset = [examples[1]]

    # This test verifies if the bootstrapping process improves the student's predictions
    student = SimpleModule()
    teacher = SimpleModule()
    assert student.output.predictor.signature.equals(teacher.output.predictor.signature)

    backend = DummyBackend(answers=[["blue"], ["blue"], ["Ring-ding-ding-ding-dingeringeding!"]])
    with dspy.settings.context(backend=backend, cache=False, lm=None):
        bootstrap = BootstrapFewShot(metric=simple_metric, max_bootstrapped_demos=1, max_labeled_demos=1)
        compiled_student = bootstrap.compile(student, teacher=teacher, trainset=trainset)

        # Check that the compiled student has the correct demos
        _, predict = next(compiled_student.named_sub_modules(Predict, skip_compiled=False))
        demos = predict.demos
        assert len(demos) == 1
        assert demos[0].input == trainset[0].input
        assert demos[0].output == trainset[0].output

        # Test the compiled student's prediction.
        prediction = compiled_student(input=trainset[0].input)
        assert prediction == trainset[0].output

        assert backend.history[-1].input_kwargs["messages"] == [
            {
                "role": "user",
                "content": textwrap.dedent(
                    """\
            Given the fields `input`, produce the fields `output`.

            ---

            Follow the following format.

            Input: ${input}
            
            Output: ${output}

            ---

            Input: What is the color of the sky?

            Output: blue

            ---

            Input: What is the color of the sky?

            Output:"""
                ),
            }
        ]


def test_regex():
    class TravelInformation(BaseModel):
        origin: str = Field(pattern=r"^[A-Z]{3}$")
        destination: str = Field(pattern=r"^[A-Z]{3}$")
        date: datetime.date

    @predictor
    def flight_information(email: str) -> TravelInformation:
        pass

    email = textwrap.dedent(
        """\
        We're excited to welcome you aboard your upcoming flight from 
        John F. Kennedy International Airport (JFK) to Los Angeles International Airport (LAX)
        on December 25, 2022. Here's everything you need to know before you take off: ...
    """
    )
    lm = DummyLM(
        [
            # Example with a bad origin code.
            '{"origin": "JF0", "destination": "LAX", "date": "2022-12-25"}',
            # Example to help the model understand
            "{...}",
            # Fixed
            '{"origin": "JFK", "destination": "LAX", "date": "2022-12-25"}',
        ]
    )

    with dspy.settings.context(lm=lm, backend=None):
        assert flight_information(email=email) == TravelInformation(
            origin="JFK", destination="LAX", date=datetime.date(2022, 12, 25)
        )


def test_regex_with_backend():
    class TravelInformation(BaseModel):
        origin: str = Field(pattern=r"^[A-Z]{3}$")
        destination: str = Field(pattern=r"^[A-Z]{3}$")
        date: datetime.date

    @predictor
    def flight_information(email: str) -> TravelInformation:
        pass

    email = textwrap.dedent(
        """\
        We're excited to welcome you aboard your upcoming flight from 
        John F. Kennedy International Airport (JFK) to Los Angeles International Airport (LAX)
        on December 25, 2022. Here's everything you need to know before you take off: ...
    """
    )

    backend = DummyBackend(
        answers=[
            ['{"origin": "JF0", "destination": "LAX", "date": "2022-12-25"}'],
            ["{...}"],
            ['{"origin": "JFK", "destination": "LAX", "date": "2022-12-25"}'],
        ]
    )
    with dspy.settings.context(backend=backend, lm=None, cache=False):
        predict = flight_information(email=email)
        assert predict == TravelInformation(origin="JFK", destination="LAX", date=datetime.date(2022, 12, 25))


def test_raises():
    class TravelInformation(BaseModel):
        origin: str = Field(pattern=r"^[A-Z]{3}$")
        destination: str = Field(pattern=r"^[A-Z]{3}$")
        date: datetime.date

    @predictor
    def flight_information(email: str) -> TravelInformation:
        pass

    lm = DummyLM(
        [
            "A list of bad inputs",
            '{"origin": "JF0", "destination": "LAX", "date": "2022-12-25"}',
            '{"origin": "JFK", "destination": "LAX", "date": "bad date"}',
        ]
    )

    with dspy.settings.context(lm=lm, backend=None):
        with pytest.raises(ValueError):
            flight_information(email="Some email")


def test_raises_with_backend():
    class TravelInformation(BaseModel):
        origin: str = Field(pattern=r"^[A-Z]{3}$")
        destination: str = Field(pattern=r"^[A-Z]{3}$")
        date: datetime.date

    @predictor
    def flight_information(email: str) -> TravelInformation:
        pass

    backend = DummyBackend(
        answers=[
            ["A list of bad inputs"],
            ['{"origin": "JF0", "destination": "LAX", "date": "2022-12-25"}'],
            ['{"origin": "JFK", "destination": "LAX", "date": "bad date"}'],
            ["..."],
            ["..."],
        ]
    )

    with dspy.settings.context(backend=backend, lm=None):
        with pytest.raises(ValueError):
            flight_information(email="Some email")


def test_multi_errors():
    class TravelInformation(BaseModel):
        origin: str = Field(pattern=r"^[A-Z]{3}$")
        destination: str = Field(pattern=r"^[A-Z]{3}$")
        date: datetime.date

    @predictor
    def flight_information(email: str) -> TravelInformation:
        pass

    lm = DummyLM(
        [
            # First origin is wrong, then destination, then all is good
            '{"origin": "JF0", "destination": "LAX", "date": "2022-12-25"}',
            "{...}",  # Example to help the model understand
            '{"origin": "JFK", "destination": "LA0", "date": "2022-12-25"}',
            "{...}",  # Example to help the model understand
            '{"origin": "JFK", "destination": "LAX", "date": "2022-12-25"}',
        ]
    )

    with dspy.settings.context(lm=lm, backend=None):
        assert flight_information(email="Some email") == TravelInformation(
            origin="JFK", destination="LAX", date=datetime.date(2022, 12, 25)
        )
        assert lm.get_convo(-1) == textwrap.dedent(
            """\
            Given the fields `email`, produce the fields `flight_information`.

            ---

            Follow the following format.

            Email: ${email}

            Past Error in Flight Information: An error to avoid in the future

            Past Error (2) in Flight Information: An error to avoid in the future

            Flight Information: ${flight_information}. Respond with a single JSON object. JSON Schema: {"properties": {"origin": {"pattern": "^[A-Z]{3}$", "title": "Origin", "type": "string"}, "destination": {"pattern": "^[A-Z]{3}$", "title": "Destination", "type": "string"}, "date": {"format": "date", "title": "Date", "type": "string"}}, "required": ["origin", "destination", "date"], "title": "TravelInformation", "type": "object"}

            ---

            Email: Some email

            Past Error in Flight Information: String should match pattern '^[A-Z]{3}$': origin (error type: string_pattern_mismatch)

            Past Error (2) in Flight Information: String should match pattern '^[A-Z]{3}$': destination (error type: string_pattern_mismatch)

            Flight Information: {"origin": "JFK", "destination": "LAX", "date": "2022-12-25"}"""
        )


def test_multi_errors_with_backend():
    class TravelInformation(BaseModel):
        origin: str = Field(pattern=r"^[A-Z]{3}$")
        destination: str = Field(pattern=r"^[A-Z]{3}$")
        date: datetime.date

    @predictor
    def flight_information(email: str) -> TravelInformation:
        pass

    backend = DummyBackend(
        answers=[
            ['{"origin": "JF0", "destination": "LAX", "date": "2022-12-25"}'],
            ["{...}"],
            ['{"origin": "JFK", "destination": "LA0", "date": "2022-12-25"}'],
            ["{...}"],
            ['{"origin": "JFK", "destination": "LAX", "date": "2022-12-25"}'],
        ]
    )

    with dspy.settings.context(backend=backend, lm=None, cache=False):
        assert flight_information(email="Some email") == TravelInformation(
            origin="JFK", destination="LAX", date=datetime.date(2022, 12, 25)
        )

        assert backend.history[-1].input_kwargs["messages"] == [
            {
                "role": "user",
                "content": textwrap.dedent(
                    """\
            Given the fields `email`, produce the fields `flight_information`.

            ---

            Follow the following format.

            Email: ${email}

            Past Error in Flight Information: An error to avoid in the future

            Past Error (2) in Flight Information: An error to avoid in the future

            Flight Information: ${flight_information}. Respond with a single JSON object. JSON Schema: {"properties": {"origin": {"pattern": "^[A-Z]{3}$", "title": "Origin", "type": "string"}, "destination": {"pattern": "^[A-Z]{3}$", "title": "Destination", "type": "string"}, "date": {"format": "date", "title": "Date", "type": "string"}}, "required": ["origin", "destination", "date"], "title": "TravelInformation", "type": "object"}

            ---

            Email: Some email

            Past Error in Flight Information: String should match pattern '^[A-Z]{3}$': origin (error type: string_pattern_mismatch)

            Past Error (2) in Flight Information: String should match pattern '^[A-Z]{3}$': destination (error type: string_pattern_mismatch)

            Flight Information:"""
                ),
            }
        ]


def test_field_validator():
    class UserDetails(BaseModel):
        name: str
        age: int

        @field_validator("name")
        @classmethod
        def validate_name(cls, v):
            if v.upper() != v:
                raise ValueError("Name must be in uppercase.")
            return v

    @predictor
    def get_user_details() -> UserDetails:
        pass

    # Keep making the mistake (lower case name) until we run
    # out of retries.
    lm = DummyLM(
        [
            '{"name": "lower case name", "age": 25}',
        ]
        * 10
    )

    with dspy.settings.context(lm=lm, backend=None):
        with pytest.raises(ValueError):
            get_user_details()

        print(lm.get_convo(-1))
        assert lm.get_convo(-1) == textwrap.dedent(
            """\
            Given the fields , produce the fields `get_user_details`.

            ---

            Follow the following format.

            Past Error in Get User Details: An error to avoid in the future
            Past Error (2) in Get User Details: An error to avoid in the future
            Get User Details: ${get_user_details}. Respond with a single JSON object. JSON Schema: {"properties": {"name": {"title": "Name", "type": "string"}, "age": {"title": "Age", "type": "integer"}}, "required": ["name", "age"], "title": "UserDetails", "type": "object"}

            ---

            Past Error in Get User Details: Value error, Name must be in uppercase.: name (error type: value_error)
            Past Error (2) in Get User Details: Value error, Name must be in uppercase.: name (error type: value_error)
            Get User Details: {"name": "lower case name", "age": 25}"""
        )


def test_field_validator_with_backend():
    class UserDetails(BaseModel):
        name: str
        age: int

        @field_validator("name")
        @classmethod
        def validate_name(cls, v):
            if v.upper() != v:
                raise ValueError("Name must be in uppercase.")
            return v

    @predictor
    def get_user_details() -> UserDetails:
        pass

    # Keep making the mistake (lower case name) until we run
    # out of retries.
    backend = DummyBackend(answers=[['{"name": "lower case name", "age": 25}']] * 10, default_params={"num_retries": 1})
    with dspy.settings.context(backend=backend, cache=True, lm=None):
        with pytest.raises(ValueError):
            get_user_details()

        assert backend.history[-1].input_kwargs["messages"] == [
            {
                "role": "user",
                "content": textwrap.dedent(
                    """\
            Given the fields , produce the fields `get_user_details`.

            ---

            Follow the following format.

            Past Error in Get User Details: An error to avoid in the future

            Past Error (2) in Get User Details: An error to avoid in the future

            Get User Details: ${get_user_details}. Respond with a single JSON object. JSON Schema: {"properties": {"name": {"title": "Name", "type": "string"}, "age": {"title": "Age", "type": "integer"}}, "required": ["name", "age"], "title": "UserDetails", "type": "object"}

            ---

            Past Error in Get User Details: Value error, Name must be in uppercase.: name (error type: value_error)

            Past Error (2) in Get User Details: Value error, Name must be in uppercase.: name (error type: value_error)

            Get User Details:"""
                ),
            }
        ]


def test_annotated_field():
    @predictor
    def test(input: Annotated[str, Field(description="description")]) -> Annotated[float, Field(gt=0, lt=1)]:
        pass

    # First try 0, which fails, then try 0.5, which passes
    lm = DummyLM(["0", "0.5"])

    with dspy.settings.context(lm=lm, backend=None):
        output = test(input="input")

        assert output == 0.5


def test_multiple_outputs():
    lm = DummyLM([str(i) for i in range(100)])

    with dspy.settings.context(lm=lm, backend=None):
        test = TypedPredictor("input -> output")
        result = test(input="input", config=dict(n=3))

        assert [completion.output for completion in result.completions] == ["0", "1", "2"]


def test_multiple_outputs_int():
    lm = DummyLM([str(i) for i in range(100)])

    with dspy.settings.context(lm=lm, backend=None):

        class TestSignature(dspy.Signature):
            input: int = dspy.InputField()
            output: int = dspy.OutputField()

        test = TypedPredictor(TestSignature)

        result = test(input=8, config=dict(n=3))
        assert [completion.output for completion in result.completions] == [0, 1, 2]


def test_multiple_outputs_int_cot():
    # Note: Multiple outputs only work when the language model "speculatively" generates all the outputs in one go.
    lm = DummyLM(
        [
            "thoughts 0\nOutput: 0\n",
            "thoughts 1\nOutput: 1\n",
            "thoughts 2\nOutput: 2\n",
        ]
    )

    with dspy.settings.context(lm=lm, backend=None):
        test = TypedChainOfThought("input:str -> output:int")

        results = test(input="8", config=dict(n=3))
        assert [completion.output for completion in results.completions]


def test_parse_type_string():
    lm = DummyLM([str(i) for i in range(100)])

    with dspy.settings.context(lm=lm, backend=None):
        test = TypedPredictor("input:int -> output:int")

        results = test(input=8, config=dict(n=3))
        assert [completion.output for completion in results.completions] == [0, 1, 2]


def test_literal():
    lm = DummyLM([f'{{"value": "{i}"}}' for i in range(100)])

    with dspy.settings.context(lm=lm, backend=None):

        @predictor
        def f() -> Literal["2", "3"]:
            pass

        assert f() == "2"


def test_literal_int():
    lm = DummyLM([f'{{"value": {i}}}' for i in range(100)])

    with dspy.settings.context(lm=lm, backend=None):

        @predictor
        def f() -> Literal[2, 3]:
            pass

        assert f() == 2


def test_fields_on_base_signature():
    class SimpleOutput(dspy.Signature):
        output: float = dspy.OutputField(gt=0, lt=1)

    lm = DummyLM(
        [
            "2.1",  # Bad output
            "0.5",  # Good output
        ]
    )

    with dspy.settings.context(lm=lm, backend=None):
        predictor = TypedPredictor(SimpleOutput)

        assert predictor().output == 0.5


def test_synthetic_data_gen():
    class SyntheticFact(BaseModel):
        fact: str = Field(..., description="a statement")
        varacity: bool = Field(..., description="is the statement true or false")

    class ExampleSignature(dspy.Signature):
        """Generate an example of a synthetic fact."""

        fact: SyntheticFact = dspy.OutputField()

    lm = DummyLM(
        [
            '{"fact": "The sky is blue", "varacity": true}',
            '{"fact": "The sky is green", "varacity": false}',
            '{"fact": "The sky is red", "varacity": true}',
            '{"fact": "The earth is flat", "varacity": false}',
            '{"fact": "The earth is round", "varacity": true}',
            '{"fact": "The earth is a cube", "varacity": false}',
        ]
    )

    with dspy.settings.context(lm=lm, backend=None):
        generator = TypedPredictor(ExampleSignature)
        examples = generator(config=dict(n=3))
        for completion in examples.completions:
            assert isinstance(completion.fact, SyntheticFact), type(completion.fact)
        assert examples.completions[0].fact == SyntheticFact(fact="The sky is blue", varacity=True)

        # If you have examples and want more
        existing_examples = [
            dspy.Example(fact="The sky is blue", varacity=True),
            dspy.Example(fact="The sky is green", varacity=False),
        ]
        trained = LabeledFewShot().compile(student=generator, trainset=existing_examples)

        augmented_examples = trained(config=dict(n=3))
        for completion in augmented_examples.completions:
            assert isinstance(completion.fact, SyntheticFact)


def test_list_input2():
    # Inspired by the Signature Optimizer

    class ScoredString(pydantic.BaseModel):
        string: str
        score: float

    class ScoredSignature(dspy.Signature):
        attempted_signatures: list[ScoredString] = dspy.InputField()
        proposed_signature: str = dspy.OutputField()

    program = TypedChainOfThought(ScoredSignature)

    lm = DummyLM(["Thoughts", "Output"])

    with dspy.settings.context(lm=lm, backend=None):
        output = program(
            attempted_signatures=[
                ScoredString(string="string 1", score=0.5),
                ScoredString(string="string 2", score=0.4),
                ScoredString(string="string 3", score=0.3),
            ]
        ).proposed_signature

        print(lm.get_convo(-1))

        assert output == "Output"

        assert lm.get_convo(-1) == textwrap.dedent(
            """\
            Given the fields `attempted_signatures`, produce the fields `proposed_signature`.

            ---

            Follow the following format.

            Attempted Signatures: ${attempted_signatures}
            Reasoning: Let's think step by step in order to ${produce the proposed_signature}. We ...
            Proposed Signature: ${proposed_signature}

            ---

            Attempted Signatures: [{"string":"string 1","score":0.5},{"string":"string 2","score":0.4},{"string":"string 3","score":0.3}]
            Reasoning: Let's think step by step in order to Thoughts
            Proposed Signature: Output"""
        )


def test_generic_signature():
    T = TypeVar("T")

    class GenericSignature(dspy.Signature, Generic[T]):
        """My signature"""

        output: T = dspy.OutputField()

    predictor = TypedPredictor(GenericSignature[int])
    assert predictor.signature.instructions == "My signature"

    lm = DummyLM(["23"])

    with dspy.settings.context(lm=lm, backend=None):
        assert predictor().output == 23


def test_field_validator_in_signature():
    class ValidatedSignature(dspy.Signature):
        a: str = dspy.OutputField()

        @pydantic.field_validator("a")
        @classmethod
        def space_in_a(cls, a: str) -> str:
            if not " " in a:
                raise ValueError("a must contain a space")
            return a

    with pytest.raises(pydantic.ValidationError):
        _ = ValidatedSignature(a="no-space")

    _ = ValidatedSignature(a="with space")


def test_lm_as_validator():
    @predictor
    def is_square(n: int) -> bool:
        """Is x a square number?"""

    def check_square(n):
        assert is_square(n=n)
        return n

    @predictor
    def next_square(n: int) -> Annotated[int, AfterValidator(check_square)]:
        """What is the next square number after n?"""

    lm = DummyLM(["3", "False", "4", "True"])

    with dspy.settings.context(lm=lm, backend=None):
        m = next_square(n=2)
        lm.inspect_history(n=2)
        assert m == 4


def test_annotated_validator():
    def is_square(n: int) -> int:
        root = n**0.5
        if not root.is_integer():
            raise ValueError(f"{n} is not a square")
        return n

    class MySignature(dspy.Signature):
        """What is the next square number after n?"""

        n: int = dspy.InputField()
        next_square: Annotated[int, AfterValidator(is_square)] = dspy.OutputField()

    lm = DummyLM(["3", "4"])

    with dspy.settings.context(lm=lm, backend=None):
        m = TypedPredictor(MySignature)(n=2).next_square
        lm.inspect_history(n=2)

        assert m == 4


def test_annotated_validator_functional():
    def is_square(n: int) -> int:
        if not (n**0.5).is_integer():
            raise ValueError(f"{n} is not a square")
        return n

    @predictor
    def next_square(n: int) -> Annotated[int, AfterValidator(is_square)]:
        """What is the next square number after n?"""

    lm = DummyLM(["3", "4"])

    with dspy.settings.context(lm=lm, backend=None):
        m = next_square(n=2)
        lm.inspect_history(n=2)

        assert m == 4


def test_demos():
    demos = [
        dspy.Example(input="What is the speed of light?", output="3e8"),
    ]
    program = LabeledFewShot(k=len(demos)).compile(
        student=dspy.TypedPredictor("input -> output"),
        trainset=[ex.with_inputs("input") for ex in demos],
    )

    lm = DummyLM(["Paris"])

    with dspy.settings.context(lm=lm, backend=None):
        assert program(input="What is the capital of France?").output == "Paris"

        assert lm.get_convo(-1) == textwrap.dedent(
            """\
            Given the fields `input`, produce the fields `output`.

            ---

            Follow the following format.

            Input: ${input}
            Output: ${output}

            ---

            Input: What is the speed of light?
            Output: 3e8

            ---

            Input: What is the capital of France?
            Output: Paris"""
        )


def _test_demos_missing_input():
    demos = [dspy.Example(input="What is the speed of light?", output="3e8")]
    program = LabeledFewShot(k=len(demos)).compile(
        student=dspy.TypedPredictor("input -> output, thoughts"),
        trainset=[ex.with_inputs("input") for ex in demos],
    )

    with dspy.settings.context(lm=DummyLM(["My thoughts", "Paris"]), backend=None):
        assert program(input="What is the capital of France?").output == "Paris"

        assert dspy.settings.lm.get_convo(-1) == textwrap.dedent(
            """\
            Given the fields `input`, produce the fields `output`.

            ---

            Follow the following format.

            Input: ${input}
            Thoughts: ${thoughts}
            Output: ${output}

            ---

            Input: What is the speed of light?
            Output: 3e8

            ---

            Input: What is the capital of France?
            Thoughts: My thoughts
            Output: Paris"""
        )
