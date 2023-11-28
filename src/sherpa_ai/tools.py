import os
import re
import urllib
import urllib.parse
import urllib.request
from typing import Any

import requests
from bs4 import BeautifulSoup
from langchain import LLMChain
from langchain.prompts import Prompt
from langchain.tools import BaseTool
from langchain.utilities import GoogleSerperAPIWrapper
from langchain.vectorstores.base import VectorStoreRetriever
from loguru import logger
from typing_extensions import Literal
from hugchat import hugchat
from hugchat.login import Login

import sherpa_ai.config as cfg
from sherpa_ai.config.task_config import AgentConfig


def get_tools(memory, config):
    tools = []

    # tools.append(ContextTool(memory=memory))
    tools.append(UserInputTool())


    if cfg.SERPER_API_KEY is not None:
        search_tool = SearchTool(config=config)
        tools.append(search_tool)
    else:
        logger.warning(
            "No SERPER_API_KEY found in environment variables, skipping SearchTool"
        )

    if cfg.HUGCHAT_EMAIL is not None and cfg.HUGCHAT_PASS is not None:
        tools.append(HugChatTool())
    else:
        logger.warning(
            "No Hugchat email and pass in environment variables, skipping Hugchat tool"
        )
    return tools


class SearchArxivTool(BaseTool):
    name = "Arxiv Search"
    description = (
        "Access all the papers from Arxiv to search for domain-specific scientific publication."
        "Only use this tool when you need information in the scientific paper."
    )

    def _run(self, query: str) -> str:
        top_k = 10

        logger.debug(f"Search query: {query}")
        query = urllib.parse.quote_plus(query)
        url = (
            "http://export.arxiv.org/api/query?search_query=all:"
            + query.strip()
            + "&start=0&max_results="
            + str(top_k)
        )
        data = urllib.request.urlopen(url)
        xml_content = data.read().decode("utf-8")

        summary_pattern = r"<summary>(.*?)</summary>"
        summaries = re.findall(summary_pattern, xml_content, re.DOTALL)
        title_pattern = r"<title>(.*?)</title>"
        titles = re.findall(title_pattern, xml_content, re.DOTALL)

        result_list = []
        for i in range(len(titles)):
            result_list.append(
                "Title: " + titles[i] + "\n" + "Summary: " + summaries[i]
            )

        logger.debug(f"Arxiv Search Result: {result_list}")

        return " ".join(result_list)

    def _arun(self, query: str) -> str:
        raise NotImplementedError("SearchArxivTool does not support async run")


class SearchTool(BaseTool):
    name = "Search"
    config = AgentConfig()
    description = (
        "Access the internet to search for the information. Only use this tool when "
        "you cannot find the information using internal search."
    )

    def augment_query(self, query) -> str:
        return query + " site:" + self.config.gsite if self.config.gsite else query

    def _run(self, query: str) -> str:
        query = self.augment_query(query)

        logger.debug(f"Search query: {query}")
        google_serper = GoogleSerperAPIWrapper()
        search_results = google_serper._google_serper_api_results(query)
        logger.debug(f"Google Search Result: {search_results}")

        # case 1: answerBox in the result dictionary
        if search_results.get("answerBox", False):
            answer_box = search_results.get("answerBox", {})
            if answer_box.get("answer"):
                answer = answer_box.get("answer")
            elif answer_box.get("snippet"):
                answer = answer_box.get("snippet").replace("\n", " ")
            elif answer_box.get("snippetHighlighted"):
                answer = answer_box.get("snippetHighlighted")
            title = search_results["organic"][0]["title"]
            link = search_results["organic"][0]["link"]

            return "Answer: " + answer + "\nLink:" + link

        # case 2: knowledgeGraph in the result dictionary
        snippets = []
        if search_results.get("knowledgeGraph", False):
            kg = search_results.get("knowledgeGraph", {})
            title = kg.get("title")
            entity_type = kg.get("type")
            if entity_type:
                snippets.append(f"{title}: {entity_type}.")
            description = kg.get("description")
            if description:
                snippets.append(description)
            for attribute, value in kg.get("attributes", {}).items():
                snippets.append(f"{title} {attribute}: {value}.")
        k = 10
        search_type: Literal["news", "search", "places", "images"] = "search"
        result_key_for_type = {
            "news": "news",
            "places": "places",
            "images": "images",
            "search": "organic",
        }
        for result in search_results[result_key_for_type[search_type]][:k]:
            if "snippet" in result:
                snippets.append(result["snippet"])
            for attribute, value in result.get("attributes", {}).items():
                snippets.append(f"{attribute}: {value}.")

            if len(snippets) == 0:
                return ["No good Google Search Result was found"]

        result = []
        for i in range(len(search_results["organic"][:10])):
            r = search_results["organic"][i]
            single_result = (
                "Description: " + r["title"] + r["snippet"] + "\nLink:" + r["link"]
            )

            result.append(single_result)
        full_result = "\n".join(result)

        # answer = " ".join(snippets)
        if (
            "knowledgeGraph" in search_results
            and "description" in search_results["knowledgeGraph"]
            and "descriptionLink" in search_results["knowledgeGraph"]
        ):
            answer = (
                "Description: "
                + search_results["knowledgeGraph"]["title"]
                + search_results["knowledgeGraph"]["description"]
                + "\nLink:"
                + search_results["knowledgeGraph"]["descriptionLink"]
            )
            full_result = answer + "\n" + full_result
        return full_result

    def _arun(self, query: str) -> str:
        raise NotImplementedError("SearchTool does not support async run")


class ContextTool(BaseTool):
    name = "Context Search"
    description = (
        "Access internal technical documentation for AI related projects, including"
        + "Fixie, LangChain, GPT index, GPTCache, GPT4ALL, autoGPT, db-GPT, AgentGPT, sherpa."
        + "Only use this tool if you need information for these projects specifically."
    )
    memory: VectorStoreRetriever

    def _run(self, query: str) -> str:
        docs = self.memory.get_relevant_documents(query)
        result = ""
        for doc in docs:
            result += (
                "Document"
                + doc.page_content
                + "\nLink:"
                + doc.metadata.get("source", "")
                + "\n"
            )

        return result

    def _arun(self, query: str) -> str:
        raise NotImplementedError("ContextTool does not support async run")


class UserInputTool(BaseTool):
    # TODO: Make an action for the user input
    name = "UserInput"
    description = (
        "Access the user input for the task."
        "You use this tool if you need more context and would like to ask clarifying questions to solve the task"
    )

    def _run(self, query: str) -> str:
        return input(query)

    def _arun(self, query: str) -> str:
        raise NotImplementedError("UserInputTool does not support async run")

class HugChatTool(BaseTool):
    name = "Hugchat"
    description = (
        "Access the user input for the task."
        "This tool is an alternative way to to ask clarifying questions to solve the task, using HuggingChat via the HugChat API."
    )
    def _run(self, query: str) -> str:

        # Log in to huggingface and grant authorization to huggingchat
        sign = Login(cfg.HUGCHAT_EMAIL, cfg.HUGCHAT_PASS)
        # Save cookies to the local directory
        cookies = sign.login()

        # Save cookies to the local directory
        cookie_path_dir = "./cookies_snapshot"
        sign.saveCookiesToDir(cookie_path_dir)

        # Create a ChatBot
        chatbot = hugchat.ChatBot(cookies=cookies.get_dict())  # or cookie_path="usercookies/<email>.json"

        query_result = chatbot.query(query,stream=cfg.HUGCHAT_MODE_STREAM_RESPONSE,web_search= cfg.HUGCHAT_MODE_WEB_SEARCH)
        return query_result

    def _arun(self, query: str) -> str:
        raise NotImplementedError("HugChat does not support async run")

