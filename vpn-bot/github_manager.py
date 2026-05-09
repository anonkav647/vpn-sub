"""
Управление файлами подписок на GitHub.
Файлы загружаются в репозиторий и доступны через raw.githubusercontent.com
"""

from github import Github, GithubException
from config import GITHUB_TOKEN, GITHUB_REPO, GITHUB_BRANCH, SUBS_FOLDER, RAW_BASE_URL


def _get_repo():
    g = Github(GITHUB_TOKEN)
    return g.get_repo(GITHUB_REPO)


def upload_subscription_file(filename: str, content: str, commit_message: str = "Update subscription") -> str:
    """
    Загрузить/обновить файл подписки на GitHub.
    Возвращает URL для доступа.
    """
    repo = _get_repo()
    file_path = f"{SUBS_FOLDER}/{filename}"

    try:
        # Пробуем обновить существующий файл
        existing = repo.get_contents(file_path, ref=GITHUB_BRANCH)
        repo.update_file(
            path=file_path,
            message=commit_message,
            content=content,
            sha=existing.sha,
            branch=GITHUB_BRANCH
        )
    except GithubException as e:
        if e.status == 404:
            # Файл не существует — создаём
            repo.create_file(
                path=file_path,
                message=commit_message,
                content=content,
                branch=GITHUB_BRANCH
            )
        else:
            raise e

    return f"{RAW_BASE_URL}/{filename}"


def delete_subscription_file(filename: str, commit_message: str = "Delete subscription") -> bool:
    """Удалить файл подписки с GitHub"""
    repo = _get_repo()
    file_path = f"{SUBS_FOLDER}/{filename}"

    try:
        existing = repo.get_contents(file_path, ref=GITHUB_BRANCH)
        repo.delete_file(
            path=file_path,
            message=commit_message,
            sha=existing.sha,
            branch=GITHUB_BRANCH
        )
        return True
    except GithubException:
        return False


def get_subscription_url(filename: str) -> str:
    """Получить URL подписки"""
    return f"{RAW_BASE_URL}/{filename}"


def file_exists(filename: str) -> bool:
    """Проверить существует ли файл"""
    repo = _get_repo()
    file_path = f"{SUBS_FOLDER}/{filename}"
    try:
        repo.get_contents(file_path, ref=GITHUB_BRANCH)
        return True
    except GithubException:
        return False